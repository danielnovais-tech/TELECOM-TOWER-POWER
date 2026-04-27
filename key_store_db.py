"""
key_store_db.py — Persistent API-key + PDF quota store.

Replaces the in-memory + key_store.json hybrid in ``stripe_billing.py`` with
a database-backed store when ``DATABASE_URL`` is set, falling back to the
JSON file otherwise (so local dev / SQLite mode keep working).

Public API mirrors the interface previously exposed by ``stripe_billing``:

    get_all_keys() -> Dict[str, Dict]
    lookup_key(api_key) -> Optional[Dict]
    upsert_key(api_key, record) -> None
    delete_key(api_key) -> None
    get_key_for_email(email) -> Optional[str]
    get_record_by_email(email) -> Optional[Dict]
    consume_pdf_quota(api_key, period, limit) -> int     # raises QuotaExceeded

Records are plain dicts with the same fields the JSON file used:
``tier``, ``owner``, ``email``, ``stripe_customer_id``, ``stripe_subscription_id``,
``created`` (= created_at), and optionally ``billing_cycle``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import psycopg2  # type: ignore[import-untyped]
    import psycopg2.extras  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

logger = logging.getLogger("key_store_db")


class QuotaExceeded(Exception):
    """Raised when a per-key per-period quota is hit."""

    def __init__(self, current: int, limit: int):
        self.current = current
        self.limit = limit
        super().__init__(f"quota exceeded: {current}/{limit}")


# ─── URL / mode detection ───────────────────────────────────────────────

_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_URL = _RAW_DATABASE_URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = bool(DATABASE_URL) and psycopg2 is not None
_STORE_PATH = Path(os.getenv("KEY_STORE_PATH", "./key_store.json"))


# ─── Postgres backend ───────────────────────────────────────────────────

class _PgBackend:
    backend = "postgres"

    def __init__(self, dsn: str):
        self.dsn = dsn

    def _conn(self):
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = False
        return conn

    @staticmethod
    def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
        rec = {
            "tier": row["tier"],
            "owner": row["owner"],
            "email": row["email"],
            "stripe_customer_id": row.get("stripe_customer_id"),
            "stripe_subscription_id": row.get("stripe_subscription_id"),
            "created": row["created_at"],
        }
        if row.get("billing_cycle"):
            rec["billing_cycle"] = row["billing_cycle"]
        # SSO mapping (columns may be absent on older schemas — guard with .get).
        if row.get("sso_enabled"):
            rec["sso_enabled"] = bool(row.get("sso_enabled"))
        if row.get("oauth_provider"):
            rec["oauth_provider"] = row.get("oauth_provider")
        if row.get("oauth_subject"):
            rec["oauth_subject"] = row.get("oauth_subject")
        raw_branding = row.get("branding")
        if raw_branding:
            try:
                rec["branding"] = json.loads(raw_branding) if isinstance(raw_branding, str) else raw_branding
            except (TypeError, ValueError):
                logger.warning("could not decode branding JSON for api_key=%s", row.get("api_key"))
        return rec

    def get_all_keys(self) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM api_keys")
            for row in cur.fetchall():
                out[row["api_key"]] = self._row_to_record(row)
        return out

    def lookup_key(self, api_key: str) -> Optional[Dict]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM api_keys WHERE api_key = %s", (api_key,))
            row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def upsert_key(self, api_key: str, record: Dict) -> None:
        now = time.time()
        created_at = record.get("created", now)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO api_keys (
                    api_key, tier, owner, email,
                    stripe_customer_id, stripe_subscription_id, billing_cycle,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (api_key) DO UPDATE SET
                    tier = EXCLUDED.tier,
                    owner = EXCLUDED.owner,
                    email = EXCLUDED.email,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    billing_cycle = EXCLUDED.billing_cycle,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    api_key,
                    record["tier"],
                    record.get("owner") or record.get("email"),
                    record["email"],
                    record.get("stripe_customer_id"),
                    record.get("stripe_subscription_id"),
                    record.get("billing_cycle"),
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def delete_key(self, api_key: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM api_keys WHERE api_key = %s", (api_key,))
            conn.commit()

    def set_branding(self, api_key: str, branding: Optional[Dict]) -> None:
        """Replace the branding JSON for ``api_key``. ``None`` clears it."""
        payload = json.dumps(branding) if branding is not None else None
        now = time.time()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET branding = %s, updated_at = %s WHERE api_key = %s",
                (payload, now, api_key),
            )
            conn.commit()

    def get_key_for_email(self, email: str) -> Optional[str]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT api_key FROM api_keys WHERE email = %s ORDER BY created_at DESC LIMIT 1",
                (email,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def get_record_by_email(self, email: str) -> Optional[Dict]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM api_keys WHERE email = %s ORDER BY created_at DESC LIMIT 1",
                (email,),
            )
            row = cur.fetchone()
        if not row:
            return None
        rec = self._row_to_record(row)
        rec["api_key"] = row["api_key"]
        return rec

    def lookup_by_oauth(self, provider: str, subject: str) -> Optional[Dict]:
        """Return ``{api_key, ...record}`` for an IdP-provided (provider, sub) pair.

        Returns ``None`` if no row matches or the columns are absent (very
        old schema). Never raises on missing columns — falls back silently
        so callers can branch on ``None``.
        """
        try:
            with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM api_keys WHERE oauth_provider = %s AND oauth_subject = %s LIMIT 1",
                    (provider, subject),
                )
                row = cur.fetchone()
        except psycopg2.errors.UndefinedColumn:  # type: ignore[attr-defined]
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("lookup_by_oauth failed: %s", exc)
            return None
        if not row:
            return None
        rec = self._row_to_record(row)
        rec["api_key"] = row["api_key"]
        return rec

    def set_sso_mapping(self, api_key: str, provider: str, subject: str) -> None:
        """Stamp an existing api_key row with its SSO identity. Idempotent."""
        now = time.time()
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE api_keys
                       SET oauth_provider = %s,
                           oauth_subject = %s,
                           sso_enabled = TRUE,
                           updated_at = %s
                     WHERE api_key = %s
                    """,
                    (provider, subject, now, api_key),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_sso_mapping failed for %s: %s", api_key, exc)

    def consume_pdf_quota(self, api_key: str, period: str, limit: int) -> int:
        """Atomically increment-or-create the (api_key, period) PDF counter.

        Raises QuotaExceeded when the resulting count would exceed ``limit``;
        in that case the counter is *not* incremented (the UPDATE is rolled back).
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pdf_usage_monthly (api_key, period, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (api_key, period) DO UPDATE
                  SET count = pdf_usage_monthly.count + 1
                RETURNING count
                """,
                (api_key, period),
            )
            new_count = cur.fetchone()[0]
            if new_count > limit:
                # Roll back the increment so we don't leak counter on quota deny.
                conn.rollback()
                raise QuotaExceeded(new_count - 1, limit)
            conn.commit()
            return new_count


# ─── JSON-file fallback backend ────────────────────────────────────────

class _JsonBackend:
    backend = "json"

    def __init__(self, path: Path):
        self.path = path
        self._mem: Dict[str, Dict] = {}
        self._initialised = False
        self._lock = threading.Lock()
        # PDF quota counters live entirely in-memory for this backend
        # (process-local state — same constraint that already applied before).
        self._pdf: Dict[str, Dict[str, int]] = {}

    def _ensure(self) -> None:
        if self._initialised:
            return
        with self._lock:
            if self._initialised:
                return
            self._initialised = True
            try:
                if self.path.exists():
                    self._mem.update(json.loads(self.path.read_text()))
                    logger.info("Loaded %d keys from %s", len(self._mem), self.path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not read key store file %s: %s", self.path, exc)

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._mem, indent=2))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist key store to %s: %s", self.path, exc)

    def get_all_keys(self) -> Dict[str, Dict]:
        self._ensure()
        with self._lock:
            return {k: dict(v) for k, v in self._mem.items()}

    def lookup_key(self, api_key: str) -> Optional[Dict]:
        self._ensure()
        with self._lock:
            v = self._mem.get(api_key)
            return dict(v) if v else None

    def upsert_key(self, api_key: str, record: Dict) -> None:
        self._ensure()
        with self._lock:
            existing_branding = (self._mem.get(api_key) or {}).get("branding")
            self._mem[api_key] = {
                "tier": record["tier"],
                "owner": record.get("owner") or record.get("email"),
                "email": record["email"],
                "stripe_customer_id": record.get("stripe_customer_id"),
                "stripe_subscription_id": record.get("stripe_subscription_id"),
                "created": record.get("created", time.time()),
            }
            if record.get("billing_cycle"):
                self._mem[api_key]["billing_cycle"] = record["billing_cycle"]
            # Preserve branding across upserts unless the caller explicitly
            # supplies one (Stripe tier-change webhooks must not wipe it).
            new_branding = record.get("branding", existing_branding)
            if new_branding is not None:
                self._mem[api_key]["branding"] = new_branding
            self._save()

    def delete_key(self, api_key: str) -> None:
        self._ensure()
        with self._lock:
            self._mem.pop(api_key, None)
            self._save()

    def set_branding(self, api_key: str, branding: Optional[Dict]) -> None:
        self._ensure()
        with self._lock:
            rec = self._mem.get(api_key)
            if rec is None:
                return
            if branding is None:
                rec.pop("branding", None)
            else:
                rec["branding"] = branding
            self._save()

    def get_key_for_email(self, email: str) -> Optional[str]:
        self._ensure()
        with self._lock:
            for k, v in self._mem.items():
                if v.get("email") == email:
                    return k
        return None

    def get_record_by_email(self, email: str) -> Optional[Dict]:
        self._ensure()
        with self._lock:
            for k, v in self._mem.items():
                if v.get("email") == email:
                    return {"api_key": k, **dict(v)}
        return None

    def lookup_by_oauth(self, provider: str, subject: str) -> Optional[Dict]:
        self._ensure()
        with self._lock:
            for k, v in self._mem.items():
                if v.get("oauth_provider") == provider and v.get("oauth_subject") == subject:
                    return {"api_key": k, **dict(v)}
        return None

    def set_sso_mapping(self, api_key: str, provider: str, subject: str) -> None:
        self._ensure()
        with self._lock:
            rec = self._mem.get(api_key)
            if rec is None:
                return
            rec["oauth_provider"] = provider
            rec["oauth_subject"] = subject
            rec["sso_enabled"] = True
            self._save()

    def consume_pdf_quota(self, api_key: str, period: str, limit: int) -> int:
        with self._lock:
            entry = self._pdf.get(api_key)
            if entry is None or entry.get("period") != period:
                entry = {"period": period, "count": 0}
                self._pdf[api_key] = entry
            if entry["count"] >= limit:
                raise QuotaExceeded(entry["count"], limit)
            entry["count"] += 1
            return entry["count"]


# ─── Singleton selection ────────────────────────────────────────────────

_backend_instance = None
_backend_lock = threading.Lock()


def get_backend():
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance
    with _backend_lock:
        if _backend_instance is None:
            if _USE_PG:
                _backend_instance = _PgBackend(DATABASE_URL)  # type: ignore[arg-type]
                logger.info("key_store_db: using PostgreSQL backend")
            else:
                _backend_instance = _JsonBackend(_STORE_PATH)
                logger.info("key_store_db: using JSON file backend (%s)", _STORE_PATH)
    return _backend_instance


# ─── Module-level convenience wrappers ──────────────────────────────────

def get_all_keys() -> Dict[str, Dict]:
    return get_backend().get_all_keys()


def lookup_key(api_key: str) -> Optional[Dict]:
    return get_backend().lookup_key(api_key)


def upsert_key(api_key: str, record: Dict) -> None:
    get_backend().upsert_key(api_key, record)


def delete_key(api_key: str) -> None:
    get_backend().delete_key(api_key)


def set_branding(api_key: str, branding: Optional[Dict]) -> None:
    get_backend().set_branding(api_key, branding)


def get_key_for_email(email: str) -> Optional[str]:
    return get_backend().get_key_for_email(email)


def get_record_by_email(email: str) -> Optional[Dict]:
    return get_backend().get_record_by_email(email)


def consume_pdf_quota(api_key: str, period: str, limit: int) -> int:
    return get_backend().consume_pdf_quota(api_key, period, limit)


def lookup_by_oauth(provider: str, subject: str) -> Optional[Dict]:
    return get_backend().lookup_by_oauth(provider, subject)


def set_sso_mapping(api_key: str, provider: str, subject: str) -> None:
    get_backend().set_sso_mapping(api_key, provider, subject)
