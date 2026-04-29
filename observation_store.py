"""
observation_store.py – Persistence for ML training labels.

Two tables:

1. ``link_observations`` – ground-truth RSSI measurements submitted via
   ``POST /coverage/observations`` or batch CSV upload. One row = one
   real point-to-point measurement.

2. ``cell_signal_samples`` – aggregated ``averageSignal`` values from
   OpenCelliD. One row per cell. The receiver location is unknown, so
   training treats the centroid as the rx location and ``range_m / 2``
   as the link distance (soft label, down-weighted).

Same backend-selection rules as ``tower_db``: PostgreSQL when
``DATABASE_URL`` is set, otherwise SQLite at ``$TOWER_DB_PATH``.
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

try:
    import psycopg2  # type: ignore[import-untyped]
    import psycopg2.extras  # type: ignore[import-untyped]
except ImportError:
    psycopg2 = None  # type: ignore[assignment]


_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("TOWER_DB_PATH", "towers.db")

DATABASE_URL = _RAW_DATABASE_URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    DATABASE_URL = DATABASE_URL.replace("postgresql+aiosqlite://", "")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = bool(DATABASE_URL)


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class _SQLiteObservationStore:
    backend = "sqlite"

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS link_observations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts            REAL    NOT NULL,
                    tower_id      TEXT,
                    tx_lat        REAL    NOT NULL,
                    tx_lon        REAL    NOT NULL,
                    tx_height_m   REAL    NOT NULL,
                    tx_power_dbm  REAL    NOT NULL,
                    tx_gain_dbi   REAL    NOT NULL DEFAULT 17.0,
                    rx_lat        REAL    NOT NULL,
                    rx_lon        REAL    NOT NULL,
                    rx_height_m   REAL    NOT NULL DEFAULT 1.5,
                    rx_gain_dbi   REAL    NOT NULL DEFAULT 0.0,
                    freq_hz       REAL    NOT NULL,
                    observed_dbm  REAL    NOT NULL,
                    source        TEXT    NOT NULL DEFAULT 'api',
                    submitted_by  TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_obs_tower
                ON link_observations(tower_id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cell_signal_samples (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    tower_id        TEXT    NOT NULL,
                    centroid_lat    REAL    NOT NULL,
                    centroid_lon    REAL    NOT NULL,
                    range_m         REAL    NOT NULL,
                    samples         INTEGER NOT NULL,
                    freq_hz         REAL    NOT NULL,
                    avg_signal_dbm  REAL    NOT NULL,
                    UNIQUE (tower_id, freq_hz)
                )
            """)

    # -------- writes --------
    def insert_observation(self, obs: Dict[str, Any]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO link_observations
                   (ts, tower_id, tx_lat, tx_lon, tx_height_m, tx_power_dbm,
                    tx_gain_dbi, rx_lat, rx_lon, rx_height_m, rx_gain_dbi,
                    freq_hz, observed_dbm, source, submitted_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    obs.get("ts") or time.time(),
                    obs.get("tower_id"),
                    obs["tx_lat"], obs["tx_lon"], obs["tx_height_m"],
                    obs["tx_power_dbm"],
                    obs.get("tx_gain_dbi") or 17.0,
                    obs["rx_lat"], obs["rx_lon"],
                    obs.get("rx_height_m") or 1.5,
                    obs.get("rx_gain_dbi") or 0.0,
                    obs["freq_hz"], obs["observed_dbm"],
                    obs.get("source") or "api",
                    obs.get("submitted_by"),
                ),
            )
            return int(cur.lastrowid or 0)

    def insert_observations_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO link_observations
                   (ts, tower_id, tx_lat, tx_lon, tx_height_m, tx_power_dbm,
                    tx_gain_dbi, rx_lat, rx_lon, rx_height_m, rx_gain_dbi,
                    freq_hz, observed_dbm, source, submitted_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        r.get("ts") or time.time(),
                        r.get("tower_id"),
                        r["tx_lat"], r["tx_lon"], r["tx_height_m"],
                        r["tx_power_dbm"],
                        r.get("tx_gain_dbi") or 17.0,
                        r["rx_lat"], r["rx_lon"],
                        r.get("rx_height_m") or 1.5,
                        r.get("rx_gain_dbi") or 0.0,
                        r["freq_hz"], r["observed_dbm"],
                        r.get("source") or "api",
                        r.get("submitted_by"),
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def upsert_cell_samples_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO cell_signal_samples
                   (tower_id, centroid_lat, centroid_lon, range_m, samples,
                    freq_hz, avg_signal_dbm)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        r["tower_id"], r["centroid_lat"], r["centroid_lon"],
                        r["range_m"], r["samples"], r["freq_hz"],
                        r["avg_signal_dbm"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    # -------- reads --------
    def iter_observations(self) -> Iterator[Dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM link_observations")
            for row in cur:
                yield dict(row)

    def iter_cell_samples(self) -> Iterator[Dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM cell_signal_samples")
            for row in cur:
                yield dict(row)

    def counts(self) -> Dict[str, int]:
        with self._conn() as conn:
            o = conn.execute("SELECT COUNT(*) FROM link_observations").fetchone()[0]
            c = conn.execute("SELECT COUNT(*) FROM cell_signal_samples").fetchone()[0]
            return {"link_observations": int(o), "cell_signal_samples": int(c)}


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------

class _PgObservationStore:
    backend = "postgresql"

    def __init__(self, dsn: str):
        if psycopg2 is None:
            raise ImportError(
                "DATABASE_URL is set but psycopg2 is not installed. "
                "Run: pip install psycopg2-binary"
            )
        self.dsn = dsn
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

    def _init_db(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS link_observations (
                        id            BIGSERIAL PRIMARY KEY,
                        ts            DOUBLE PRECISION NOT NULL,
                        tower_id      TEXT,
                        tx_lat        DOUBLE PRECISION NOT NULL,
                        tx_lon        DOUBLE PRECISION NOT NULL,
                        tx_height_m   DOUBLE PRECISION NOT NULL,
                        tx_power_dbm  DOUBLE PRECISION NOT NULL,
                        tx_gain_dbi   DOUBLE PRECISION NOT NULL DEFAULT 17.0,
                        rx_lat        DOUBLE PRECISION NOT NULL,
                        rx_lon        DOUBLE PRECISION NOT NULL,
                        rx_height_m   DOUBLE PRECISION NOT NULL DEFAULT 1.5,
                        rx_gain_dbi   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        freq_hz       DOUBLE PRECISION NOT NULL,
                        observed_dbm  DOUBLE PRECISION NOT NULL,
                        source        TEXT NOT NULL DEFAULT 'api',
                        submitted_by  TEXT
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_obs_tower
                    ON link_observations(tower_id)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS cell_signal_samples (
                        id              BIGSERIAL PRIMARY KEY,
                        tower_id        TEXT NOT NULL,
                        centroid_lat    DOUBLE PRECISION NOT NULL,
                        centroid_lon    DOUBLE PRECISION NOT NULL,
                        range_m         DOUBLE PRECISION NOT NULL,
                        samples         INTEGER NOT NULL,
                        freq_hz         DOUBLE PRECISION NOT NULL,
                        avg_signal_dbm  DOUBLE PRECISION NOT NULL,
                        UNIQUE (tower_id, freq_hz)
                    )
                """)

    def insert_observation(self, obs: Dict[str, Any]) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO link_observations
                       (ts, tower_id, tx_lat, tx_lon, tx_height_m, tx_power_dbm,
                        tx_gain_dbi, rx_lat, rx_lon, rx_height_m, rx_gain_dbi,
                        freq_hz, observed_dbm, source, submitted_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (
                        obs.get("ts") or time.time(),
                        obs.get("tower_id"),
                        obs["tx_lat"], obs["tx_lon"], obs["tx_height_m"],
                        obs["tx_power_dbm"],
                        obs.get("tx_gain_dbi") or 17.0,
                        obs["rx_lat"], obs["rx_lon"],
                        obs.get("rx_height_m") or 1.5,
                        obs.get("rx_gain_dbi") or 0.0,
                        obs["freq_hz"], obs["observed_dbm"],
                        obs.get("source") or "api",
                        obs.get("submitted_by"),
                    ),
                )
                return int(cur.fetchone()[0])

    def insert_observations_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        records = [
            (
                r.get("ts") or time.time(),
                r.get("tower_id"),
                r["tx_lat"], r["tx_lon"], r["tx_height_m"],
                r["tx_power_dbm"],
                r.get("tx_gain_dbi") or 17.0,
                r["rx_lat"], r["rx_lon"],
                r.get("rx_height_m") or 1.5,
                r.get("rx_gain_dbi") or 0.0,
                r["freq_hz"], r["observed_dbm"],
                r.get("source") or "api",
                r.get("submitted_by"),
            )
            for r in rows
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO link_observations
                       (ts, tower_id, tx_lat, tx_lon, tx_height_m, tx_power_dbm,
                        tx_gain_dbi, rx_lat, rx_lon, rx_height_m, rx_gain_dbi,
                        freq_hz, observed_dbm, source, submitted_by)
                       VALUES %s""",
                    records,
                )
        return len(rows)

    def upsert_cell_samples_many(self, rows: Iterable[Dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        records = [
            (
                r["tower_id"], r["centroid_lat"], r["centroid_lon"],
                r["range_m"], r["samples"], r["freq_hz"],
                r["avg_signal_dbm"],
            )
            for r in rows
        ]
        with self._conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO cell_signal_samples
                       (tower_id, centroid_lat, centroid_lon, range_m, samples,
                        freq_hz, avg_signal_dbm)
                       VALUES %s
                       ON CONFLICT (tower_id, freq_hz) DO UPDATE SET
                           centroid_lat   = EXCLUDED.centroid_lat,
                           centroid_lon   = EXCLUDED.centroid_lon,
                           range_m        = EXCLUDED.range_m,
                           samples        = EXCLUDED.samples,
                           avg_signal_dbm = EXCLUDED.avg_signal_dbm""",
                    records,
                )
        return len(rows)

    def iter_observations(self) -> Iterator[Dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM link_observations")
                for row in cur:
                    yield dict(row)

    def iter_cell_samples(self) -> Iterator[Dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM cell_signal_samples")
                for row in cur:
                    yield dict(row)

    def counts(self) -> Dict[str, int]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM link_observations")
                o = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM cell_signal_samples")
                c = cur.fetchone()[0]
                return {"link_observations": int(o), "cell_signal_samples": int(c)}


def ObservationStore(db_path: str = DB_PATH) -> "_PgObservationStore | _SQLiteObservationStore":
    if _USE_PG and DATABASE_URL is not None:
        return _PgObservationStore(dsn=DATABASE_URL)
    return _SQLiteObservationStore(db_path=db_path)
