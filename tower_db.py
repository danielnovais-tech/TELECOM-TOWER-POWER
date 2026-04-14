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
import math
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2  # type: ignore[import-untyped]
    import psycopg2.extras  # type: ignore[import-untyped]
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

# ── Backend detection ────────────────────────────────────────────
_RAW_DATABASE_URL = os.getenv("DATABASE_URL")          # e.g. postgres://user:pw@host:5432/db
DB_PATH = os.getenv("TOWER_DB_PATH", "towers.db")  # SQLite fallback

# Normalise URL: strip SQLAlchemy async driver suffixes so psycopg2 can connect
DATABASE_URL = _RAW_DATABASE_URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    DATABASE_URL = DATABASE_URL.replace("postgresql+aiosqlite://", "")
    # Also handle the short postgres:// form
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = bool(DATABASE_URL)

if _USE_PG and psycopg2 is None:
    raise ImportError(
        "DATABASE_URL is set but psycopg2 is not installed.  "
        "Run:  pip install psycopg2-binary"
    )


# ── SQLite backend ───────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in km (shared by both backends)."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_towers_operator
                ON towers(operator)
            """)

    # ---- write --------------------------------------------------

    def upsert(self, tower_dict: Dict[str, Any]) -> None:
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

    def get(self, tower_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM towers WHERE id = ?", (tower_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_all(self, operator: Optional[str] = None,
                 limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if operator:
                rows = conn.execute(
                    "SELECT * FROM towers WHERE operator = ? LIMIT ? OFFSET ?",
                    (operator, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM towers LIMIT ? OFFSET ?", (limit, offset)
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM towers").fetchone()[0]

    def find_nearest(self, lat: float, lon: float,
                     operator: Optional[str] = None,
                     limit: int = 5) -> List[Dict[str, Any]]:
        """Return the *limit* closest towers sorted by haversine distance."""
        rows = self.list_all(operator=operator, limit=10000)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for r in rows:
            d = _haversine_km(lat, lon, r["lat"], r["lon"])
            scored.append((d, r))
        scored.sort(key=lambda x: x[0])
        return [r for _, r in scored[:limit]]

    def upsert_many(self, tower_dicts: List[Dict[str, Any]]) -> int:
        """Bulk-insert/update towers. Returns count of rows written."""
        with self._conn() as conn:
            for td in tower_dicts:
                bands = td["bands"]
                if isinstance(bands, list):
                    bands = json.dumps(bands)
                conn.execute(
                    """INSERT OR REPLACE INTO towers
                       (id, lat, lon, height_m, operator, bands, power_dbm)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (td["id"], td["lat"], td["lon"], td["height_m"],
                     td["operator"], bands, td.get("power_dbm", 43.0)),
                )
        return len(tower_dicts)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
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
        assert psycopg2 is not None
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
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_towers_operator
                    ON towers(operator)
                """)

    # ---- write --------------------------------------------------

    def upsert(self, tower_dict: Dict[str, Any]) -> None:
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

    def get(self, tower_id: str) -> Optional[Dict[str, Any]]:
        assert psycopg2 is not None
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM towers WHERE id = %s", (tower_id,))
                row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_all(self, operator: Optional[str] = None,
                 limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
        assert psycopg2 is not None
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if operator:
                    cur.execute(
                        "SELECT * FROM towers WHERE operator = %s LIMIT %s OFFSET %s",
                        (operator, limit, offset),
                    )
                else:
                    cur.execute("SELECT * FROM towers LIMIT %s OFFSET %s", (limit, offset))
                rows = cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM towers")
                row = cur.fetchone()
                return row[0] if row else 0

    def find_nearest(self, lat: float, lon: float,
                     operator: Optional[str] = None,
                     limit: int = 5) -> List[Dict[str, Any]]:
        """Return the *limit* closest towers sorted by haversine distance."""
        rows = self.list_all(operator=operator, limit=10000)
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for r in rows:
            d = _haversine_km(lat, lon, r["lat"], r["lon"])
            scored.append((d, r))
        scored.sort(key=lambda x: x[0])
        return [r for _, r in scored[:limit]]

    def upsert_many(self, tower_dicts: List[Dict[str, Any]]) -> int:
        """Bulk-insert/update towers. Returns count of rows written."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                for td in tower_dicts:
                    bands = td["bands"]
                    if isinstance(bands, list):
                        bands = json.dumps(bands)
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
                        (td["id"], td["lat"], td["lon"], td["height_m"],
                         td["operator"], bands, td.get("power_dbm", 43.0)),
                    )
        return len(tower_dicts)

    @staticmethod
    def _row_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(row)
        if isinstance(d["bands"], str):
            d["bands"] = json.loads(d["bands"])
        return d


# ── Factory: public API ──────────────────────────────────────────

def TowerStore(db_path: str = DB_PATH) -> "_PgStore | _SQLiteStore":
    """Return the appropriate store backend.

    * If ``DATABASE_URL`` is set → PostgreSQL (shared, HA-ready).
    * Otherwise → SQLite (local file, single-process).
    """
    if _USE_PG and DATABASE_URL is not None:
        return _PgStore(dsn=DATABASE_URL)
    return _SQLiteStore(db_path=db_path)
