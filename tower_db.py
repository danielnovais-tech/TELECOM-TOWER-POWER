"""
tower_db.py – SQLite persistence layer for tower data.

Provides a TowerStore class used by the API to persist towers
across restarts.  Thread-safe via SQLite's internal locking.
"""

import json
import os
import sqlite3
from typing import Dict, List, Optional


DB_PATH = os.getenv("TOWER_DB_PATH", "towers.db")


class TowerStore:
    """Thin wrapper around a SQLite database for tower CRUD."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

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
        """Insert or replace a tower.  *tower_dict* must contain the
        standard tower fields; ``bands`` may be a list (serialised to JSON)."""
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

    def list_all(self, operator: Optional[str] = None, limit: int = 1000) -> List[dict]:
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

    # ---- helpers ------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["bands"] = json.loads(d["bands"])
        return d
