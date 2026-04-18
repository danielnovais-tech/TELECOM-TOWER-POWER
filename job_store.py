"""
job_store.py – Persistent job queue backed by SQLite / PostgreSQL.

Tracks batch PDF jobs: status, progress, metadata, and result file paths.
Uses the same DATABASE_URL / TOWER_DB_PATH detection as tower_db.py.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

try:
    import psycopg2  # type: ignore[import-untyped]
    import psycopg2.extras  # type: ignore[import-untyped]
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("TOWER_DB_PATH", "towers.db")  # reuse same DB file
JOB_RESULTS_DIR = os.getenv("JOB_RESULTS_DIR", "./job_results")

# Normalise URL: strip SQLAlchemy async driver suffixes so psycopg2 can connect
DATABASE_URL = _RAW_DATABASE_URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    DATABASE_URL = DATABASE_URL.replace("postgresql+aiosqlite://", "")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = bool(DATABASE_URL)

# Ensure results directory exists
os.makedirs(JOB_RESULTS_DIR, exist_ok=True)


# ── SQLite backend ───────────────────────────────────────────────

class _SQLiteJobStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.backend = "sqlite"
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id          TEXT PRIMARY KEY,
                    status      TEXT NOT NULL DEFAULT 'queued',
                    progress    INTEGER NOT NULL DEFAULT 0,
                    total       INTEGER NOT NULL DEFAULT 0,
                    tower_id    TEXT NOT NULL,
                    receivers   TEXT NOT NULL,
                    result_path TEXT,
                    error       TEXT,
                    api_key     TEXT,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                )
            """)

    def create_job(self, job_id: str, tower_id: str,
                   receivers_json: str, total: int,
                   api_key: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        row = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "total": total,
            "tower_id": tower_id,
            "receivers": receivers_json,
            "result_path": None,
            "error": None,
            "api_key": api_key,
            "created_at": now,
            "updated_at": now,
        }
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO batch_jobs
                   (id, status, progress, total, tower_id, receivers,
                    result_path, error, api_key, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], row["status"], row["progress"], row["total"],
                 row["tower_id"], row["receivers"], row["result_path"],
                 row["error"], row["api_key"], row["created_at"], row["updated_at"]),
            )
        return row

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            r = conn.execute(
                "SELECT * FROM batch_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(r) if r else None

    def claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest queued job (set status='running')."""
        now = time.time()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM batch_jobs WHERE status = 'queued' "
                "ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE batch_jobs SET status = 'running', updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (now, row["id"]),
            )
        return dict(row)

    def update_progress(self, job_id: str, progress: int) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE batch_jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (progress, now, job_id),
            )

    def complete_job(self, job_id: str, result_path: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE batch_jobs SET status = 'completed', result_path = ?, "
                "updated_at = ? WHERE id = ?",
                (result_path, now, job_id),
            )

    def fail_job(self, job_id: str, error: str) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE batch_jobs SET status = 'failed', error = ?, "
                "updated_at = ? WHERE id = ?",
                (error, now, job_id),
            )

    def list_jobs(self, status: Optional[str] = None,
                  limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM batch_jobs WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM batch_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def list_jobs_by_api_key(self, api_key: str,
                            limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, status, progress, total, tower_id, result_path, "
                "error, created_at, updated_at FROM batch_jobs "
                "WHERE api_key = ? ORDER BY created_at DESC LIMIT ?",
                (api_key, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def heartbeat_job(self, job_id: str) -> None:
        """Touch updated_at for a running job to signal the worker is alive."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "UPDATE batch_jobs SET updated_at = ? WHERE id = ?",
                (now, job_id),
            )

    def release_stale_jobs(self, max_age_seconds: int = 300) -> int:
        """Reset running jobs whose updated_at is older than max_age_seconds
        back to 'queued' so another worker can pick them up."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE batch_jobs SET status = 'queued', error = NULL, "
                "updated_at = ? "
                "WHERE status = 'running' AND updated_at < ?",
                (time.time(), cutoff),
            )
            return cur.rowcount


# ── PostgreSQL backend ───────────────────────────────────────────

class _PgJobStore:
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

    def _init_db(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS batch_jobs (
                        id          TEXT PRIMARY KEY,
                        status      TEXT NOT NULL DEFAULT 'queued',
                        progress    INTEGER NOT NULL DEFAULT 0,
                        total       INTEGER NOT NULL DEFAULT 0,
                        tower_id    TEXT NOT NULL,
                        receivers   TEXT NOT NULL,
                        result_path TEXT,
                        error       TEXT,
                        api_key     TEXT,
                        created_at  DOUBLE PRECISION NOT NULL,
                        updated_at  DOUBLE PRECISION NOT NULL
                    )
                """)

    def create_job(self, job_id: str, tower_id: str,
                   receivers_json: str, total: int,
                   api_key: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        row = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "total": total,
            "tower_id": tower_id,
            "receivers": receivers_json,
            "result_path": None,
            "error": None,
            "api_key": api_key,
            "created_at": now,
            "updated_at": now,
        }
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO batch_jobs
                       (id, status, progress, total, tower_id, receivers,
                        result_path, error, api_key, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (row["id"], row["status"], row["progress"], row["total"],
                     row["tower_id"], row["receivers"], row["result_path"],
                     row["error"], row["api_key"], row["created_at"], row["updated_at"]),
                )
        return row

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        assert psycopg2 is not None
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM batch_jobs WHERE id = %s", (job_id,))
                r = cur.fetchone()
        return dict(r) if r else None

    def claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest queued job using FOR UPDATE SKIP LOCKED."""
        assert psycopg2 is not None
        now = time.time()
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM batch_jobs WHERE status = 'queued' "
                    "ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    "UPDATE batch_jobs SET status = 'running', updated_at = %s "
                    "WHERE id = %s",
                    (now, row["id"]),
                )
        return dict(row)

    def update_progress(self, job_id: str, progress: int) -> None:
        now = time.time()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET progress = %s, updated_at = %s "
                    "WHERE id = %s",
                    (progress, now, job_id),
                )

    def complete_job(self, job_id: str, result_path: str) -> None:
        now = time.time()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET status = 'completed', result_path = %s, "
                    "updated_at = %s WHERE id = %s",
                    (result_path, now, job_id),
                )

    def fail_job(self, job_id: str, error: str) -> None:
        now = time.time()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET status = 'failed', error = %s, "
                    "updated_at = %s WHERE id = %s",
                    (error, now, job_id),
                )

    def list_jobs(self, status: Optional[str] = None,
                  limit: int = 50) -> List[Dict[str, Any]]:
        assert psycopg2 is not None
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if status:
                    cur.execute(
                        "SELECT * FROM batch_jobs WHERE status = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (status, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM batch_jobs ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def list_jobs_by_api_key(self, api_key: str,
                            limit: int = 50) -> List[Dict[str, Any]]:
        assert psycopg2 is not None
        with self._conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, status, progress, total, tower_id, result_path, "
                    "error, created_at, updated_at FROM batch_jobs "
                    "WHERE api_key = %s ORDER BY created_at DESC LIMIT %s",
                    (api_key, limit),
                )
                rows = cur.fetchall()
        return [dict(r) for r in rows]

    def heartbeat_job(self, job_id: str) -> None:
        """Touch updated_at for a running job to signal the worker is alive."""
        now = time.time()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET updated_at = %s WHERE id = %s",
                    (now, job_id),
                )

    def release_stale_jobs(self, max_age_seconds: int = 300) -> int:
        """Reset running jobs whose updated_at is older than max_age_seconds
        back to 'queued' so another worker can pick them up."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET status = 'queued', error = NULL, "
                    "updated_at = %s "
                    "WHERE status = 'running' AND updated_at < %s",
                    (time.time(), cutoff),
                )
                return cur.rowcount

    def fail_stale_jobs(self, max_age_seconds: int = 600) -> int:
        """Mark running jobs older than *max_age_seconds* as failed."""
        cutoff = time.time() - max_age_seconds
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE batch_jobs SET status = 'failed', "
                    "error = 'stale: recovered on startup', updated_at = %s "
                    "WHERE status = 'running' AND updated_at < %s",
                    (time.time(), cutoff),
                )
                return cur.rowcount


# ── Factory ──────────────────────────────────────────────────────

def JobStore(db_path: str = DB_PATH) -> "_PgJobStore | _SQLiteJobStore":
    """Return a job store using PostgreSQL or SQLite."""
    if _USE_PG and DATABASE_URL is not None:
        return _PgJobStore(dsn=DATABASE_URL)
    return _SQLiteJobStore(db_path=db_path)
