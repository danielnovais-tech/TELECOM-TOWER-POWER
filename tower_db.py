"""
tower_db.py – Persistence layer for tower data (SQLite / PostgreSQL).

Automatically selects PostgreSQL when the DATABASE_URL env-var is set,
otherwise falls back to a local SQLite file.  Both backends expose the
same ``TowerStore`` interface so the rest of the codebase is unchanged.

PostgreSQL is required for multi-replica / high-availability deployments
where a shared database is needed (SQLite cannot be shared across
processes on separate hosts).
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import List, Optional

# ── Backend detection ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")          # e.g. postgres://user:pw@host:5432/db
DB_PATH = os.getenv("TOWER_DB_PATH", "towers.db")  # SQLite fallback

_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError(
            "DATABASE_URL is set but psycopg2 is not installed.  "
            "Run:  pip install psycopg2-binary"
        )


# ── SQLite backend ───────────────────────────────────────────────

class _SQLiteStore:
    """Thin wrapper around a SQLite database for tower CRUD."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
        self.backend = "sqlite"

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS towers (
                    id         TEXT PRIMARY KEY,
                    lat        REAL NOT NULL,
                    lon        REAL NOT NULL,
                    height_m   REAL NOT NULL,
                    operator   TEXT NOT NULL,
                    bands      TEXT NOT NULL,
                    power_dbm  REAL NOT NULL DEFAULT 43.0
                )
            """)

    # ---- write --------------------------------------------------

    def upsert(self, tower_dict: dict) -> None:
        bands = tower_dict["bands"]
        if isinstance(bands, list):
            bands = json.dumps(bands)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO towers
                   (id, lat, lon, height_m, operator, bands, power_dbm)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    tower_dict["id"],
                    tower_dict["lat"],
                    tower_dict["lon"],
                    tower_dict["height_m"],
                    tower_dict["operator"],
                    bands,
                    tower_dict.get("power_dbm", 43.0),
                ),
            )

    def delete(self, tower_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM towers WHERE id = ?", (tower_id,))
            return cur.rowcount > 0

    # ---- read ---------------------------------------------------

    def get(self, tower_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM towers WHERE id = ?", (tower_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_all(self, operator: Optional[str] = None,
                 limit: int = 1000) -> List[dict]:
        with self._conn() as conn:
            if operator:
                rows = conn.execute(
                    "SELECT * FROM towers WHERE operator = ? LIMIT ?",
                    (operator, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM towers LIMIT ?", (limit,)
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM towers").fetchone()[0]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["bands"] = json.loads(d["bands"])
        return d


# ── PostgreSQL backend ───────────────────────────────────────────

class _PgStore:
    """Tower CRUD backed by PostgreSQL via psycopg2."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.backend = "postgresql"
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = psycopg2.connect(self.dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS towers (
                        id         TEXT PRIMARY KEY,
                        lat        DOUBLE PRECISION NOT NULL,
                        lon        DOUBLE PRECISION NOT NULL,
                        height_m   DOUBLE PRECISION NOT NULL,
                        operator   TEXT NOT NULL,
                        bands      TEXT NOT NULL,
                        power_dbm  DOUBLE PRECISION NOT NULL DEFAULT 43.0
                    )
                """)

    # ---- write --------------------------------------------------

    def upsert(self, tower_dict: dict) -> None:
        bands = tower_dict["bands"]
        if isinstance(bands, list):
            bands = json.dumps(bands)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO towers
                       (id, lat, lon, height_m, operator, bands, power_dbm)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                           lat       = EXCLUDED.lat,
                           lon       = EXCLUDED.lon,
                           height_m  = EXCLUDED.height_m,
                           operator  = EXCLUDED.operator,
                           bands     = EXCLUDED.bands,
                           power_dbm = EXCLUDED.power_dbm""",
                    (
                        tower_dict["id"],
                        tower_dict["lat"],
                        tower_dict["lon"],
                        tower_dict["height_m"],
                        tower_dict["operator"],
                        bands,
                        tower_dict.get("power_dbm", 43.0),
                    ),
                )

    def delete(self, tower_id: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM towers WHERE id = %s", (tower_id,))
                return cur.rowcount > 0

    # ---- read ---------------------------------------------------

    def get(self, tower_id: str) -> Optional[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM towers WHERE id = %s", (tower_id,))
                row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_all(self, operator: Optional[str] = None,
                 limit: int = 1000) -> List[dict]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if operator:
                    cur.execute(
                        "SELECT * FROM towers WHERE operator = %s LIMIT %s",
                        (operator, limit),
                    )
                else:
                    cur.execute("SELECT * FROM towers LIMIT %s", (limit,))
                rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM towers")
                return cur.fetchone()[0]

    @staticmethod
    def _row_to_dict(row: dict) -> dict:
        d = dict(row)
        if isinstance(d["bands"], str):
            d["bands"] = json.loads(d["bands"])
        return d


# ── Factory: public API ──────────────────────────────────────────

def TowerStore(**kwargs):
    """Return the appropriate store backend.

    * If ``DATABASE_URL`` is set → PostgreSQL (shared, HA-ready).
    * Otherwise → SQLite (local file, single-process).
    """
    if _USE_PG:
        return _PgStore(dsn=DATABASE_URL)
    return _SQLiteStore(db_path=kwargs.get("db_path", DB_PATH))
