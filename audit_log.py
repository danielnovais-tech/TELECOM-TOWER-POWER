# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Append-only tenant audit log.

Design constraints:

* **Never raise into the request path.** A failing audit insert MUST NOT
  500 the user's API call. All write paths swallow exceptions and log a
  warning. (Compliance auditors prefer "missing rows are visible in
  monitoring" over "the app crashed and we lost the request".)
* **Async-first**, with a sync helper for the few code paths that are
  still sync (``plan_repeater_chain``).
* **Best-effort durability**: writes go to PostgreSQL when ``DATABASE_URL``
  is set, otherwise to an in-memory deque (CI / local dev). The deque
  is also queryable via :func:`recent_for_key` so unit tests pass without
  a database.
* **Tenant scoping at read time**: :func:`recent_for_key` only ever
  returns rows owned by the calling api_key. Admin (``owner='system'``)
  is the only path that can read across tenants and is intentionally
  not exposed via the public HTTP surface.

Schema is created by Alembic migration ``a8e7f4d521b6_add_audit_log``.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cap the in-memory buffer so a misconfigured CI run doesn't OOM.
_MEM_MAX = int(os.getenv("AUDIT_MEM_MAX", "5000"))
# Cap metadata JSON size so a malicious caller can't blow up the table.
_META_MAX_BYTES = 4096

# ---------------------------------------------------------------------------
# Competitive-intelligence hardening
# ---------------------------------------------------------------------------
#
# Several audited actions reference business-sensitive identifiers (most
# notably ``tower_id`` in ``batch.create`` metadata, which can geolocate
# a customer's expansion plans before any public announcement). Storing
# these in cleartext exposes the data to:
#
#   * nightly Postgres → S3 backups (longer retention than the live DB)
#   * any admin / impersonation read of the audit log
#   * legal / regulatory subpoenas that ask for "all audit data"
#
# :func:`hmac_target` replaces those values with a per-tenant HMAC. The
# HMAC key combines a server-side pepper (``AUDIT_TARGET_HMAC_PEPPER``,
# loaded from a secret file or env) with the tenant's own ``api_key``.
# A tenant reading their own audit log via :func:`recent_for_key` always
# sees a stable identifier (same ``tower_id`` ⇒ same hash), but an
# admin or a subpoena holding only the audit table cannot reverse the
# hash without compelling the pepper AND the per-tenant api_key.
#
# When the pepper is unset (local dev / CI) the function is a no-op so
# tests stay deterministic.

_PEPPER_FILE = "/run/secrets/audit_target_hmac_pepper"
_PEPPER_ENV = "AUDIT_TARGET_HMAC_PEPPER"


def _load_pepper() -> bytes:
    try:
        if os.path.exists(_PEPPER_FILE):
            with open(_PEPPER_FILE, "rb") as fh:
                return fh.read().strip()
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(_PEPPER_ENV, "").strip().encode("utf-8")


_HMAC_PEPPER: bytes = _load_pepper()


def hmac_target(value: Any, api_key: Optional[str] = None) -> str:
    """Return a stable, non-reversible token for ``value``.

    When ``AUDIT_TARGET_HMAC_PEPPER`` is configured, returns
    ``"h:" + first 16 hex chars of HMAC-SHA256(pepper||api_key, value)``.
    Otherwise (dev / CI) returns ``str(value)`` unchanged so tests stay
    deterministic. ``None``/empty inputs are returned as the empty
    string regardless of configuration.
    """
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if not _HMAC_PEPPER:
        return s
    key = _HMAC_PEPPER + b"\x00" + (api_key or "").encode("utf-8")
    digest = hmac.new(key, s.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"h:{digest[:16]}"


def redact_for_log(value: Any) -> str:
    """Return a non-reversible short token suitable for stdout / CloudWatch logs.

    Differs from :func:`hmac_target` in that it does NOT take a per-tenant
    ``api_key`` (call sites in worker / coverage code don't have one handy)
    and produces an even shorter prefix to keep log lines compact. When the
    pepper is unset, returns the literal ``"<redacted>"`` so dev logs do not
    accidentally leak data when secrets are missing.
    """
    if value is None or value == "":
        return ""
    s = str(value)
    if not _HMAC_PEPPER:
        return "<redacted>"
    digest = hmac.new(_HMAC_PEPPER, s.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"r:{digest[:10]}"


# ---------------------------------------------------------------------------
# Envelope encryption (KMS) for ``metadata_json``
# ---------------------------------------------------------------------------
#
# Even after HMAC-pseudonymising the obvious ``tower_id`` field, audit
# rows accumulate free-form ``metadata`` that may carry sensitive
# fragments (admin justifications, IP hints, request payloads). To
# keep ``audit_log.metadata_json`` readable only by the running app
# (not by anyone with cold DB / backup access), we wrap each row's
# JSON with AWS KMS envelope encryption:
#
#   * GenerateDataKey returns a 256-bit DEK (plaintext + KMS-wrapped).
#   * The DEK is cached in process memory for ``KMS_DEK_TTL_SECONDS``
#     (default 3600) and used to AES-GCM-encrypt up to
#     ``KMS_DEK_MAX_USES`` rows (default 1000) before rotating, so KMS
#     calls stay below ~25 / day at typical traffic.
#   * Each row stores ``"kms:v1:" + base64(wrapped_len(2) || wrapped ||
#     nonce(12) || ciphertext+tag)``. The wrapped DEK is co-located so
#     a row can be decrypted independently of the cache state.
#
# Decryption caches plaintext DEKs by their wrapped-bytes key so a
# scan of N rows (e.g. ``recent_for_key``) only triggers a small
# constant number of KMS Decrypt calls.
#
# Disabled in dev / CI: when ``AUDIT_KMS_KEY_ID`` is unset the
# serializer falls back to plain JSON. Existing cleartext rows remain
# readable; the ``scripts/audit_log_encrypt.py`` CLI migrates them
# in-place.
#
# IAM requirement: the API task role needs ``kms:GenerateDataKey`` and
# ``kms:Decrypt`` on the configured CMK.

_KMS_KEY_ID = os.getenv("AUDIT_KMS_KEY_ID", "").strip()
_KMS_REGION = os.getenv("AUDIT_KMS_REGION", os.getenv("AWS_REGION", "")).strip()
_KMS_DEK_TTL = float(os.getenv("KMS_DEK_TTL_SECONDS", "3600"))
_KMS_DEK_MAX_USES = int(os.getenv("KMS_DEK_MAX_USES", "1000"))

_KMS_PREFIX = "kms:v1:"

_kms_lock = threading.Lock()
_kms_dek_cache: Dict[str, Tuple[bytes, float]] = {}  # wrapped -> (plaintext, exp_ts)
_kms_active: Optional[Tuple[bytes, bytes, float, int]] = None  # (wrapped, plaintext, exp_ts, uses_left)


def _kms_client():
    """Return a boto3 KMS client, or None if boto3 / creds unavailable.

    Caches per-process. Best-effort: if KMS access fails for any reason
    (missing creds, network, permissions) the encryptor degrades to
    plain JSON and emits a warning so monitoring catches it.
    """
    try:
        import boto3  # type: ignore
    except Exception:
        return None
    kwargs: Dict[str, Any] = {}
    if _KMS_REGION:
        kwargs["region_name"] = _KMS_REGION
    try:
        return boto3.client("kms", **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KMS client init failed: %s", exc)
        return None


def _get_active_dek() -> Optional[Tuple[bytes, bytes]]:
    """Return ``(wrapped, plaintext)`` for the current write-side DEK.

    Generates a new DEK via ``kms:GenerateDataKey`` when the cached one
    is missing, expired, or exhausted. Returns None when KMS is
    unavailable, signalling the caller to fall back to plain JSON.
    """
    global _kms_active
    if not _KMS_KEY_ID:
        return None
    now = time.time()
    with _kms_lock:
        if _kms_active is not None:
            wrapped, plaintext, exp, uses_left = _kms_active
            if exp > now and uses_left > 0:
                _kms_active = (wrapped, plaintext, exp, uses_left - 1)
                return wrapped, plaintext
        client = _kms_client()
        if client is None:
            return None
        try:
            resp = client.generate_data_key(
                KeyId=_KMS_KEY_ID,
                KeySpec="AES_256",
                EncryptionContext={"purpose": "audit_log.metadata_json"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("kms:GenerateDataKey failed: %s", exc)
            return None
        wrapped = bytes(resp["CiphertextBlob"])
        plaintext = bytes(resp["Plaintext"])
        exp = now + _KMS_DEK_TTL
        _kms_active = (wrapped, plaintext, exp, _KMS_DEK_MAX_USES - 1)
        # Also seed the read-side cache so a same-process decrypt is free.
        _kms_dek_cache[wrapped] = (plaintext, exp)
        return wrapped, plaintext


def _decrypt_dek(wrapped: bytes) -> Optional[bytes]:
    """Unwrap ``wrapped`` via KMS, with an in-process cache.

    Returns None if KMS is unavailable; the caller should leave the row
    metadata as ``{"_decrypt_error": True}`` rather than 500ing.
    """
    now = time.time()
    with _kms_lock:
        cached = _kms_dek_cache.get(wrapped)
        if cached and cached[1] > now:
            return cached[0]
        client = _kms_client()
        if client is None:
            return None
        try:
            resp = client.decrypt(
                CiphertextBlob=wrapped,
                EncryptionContext={"purpose": "audit_log.metadata_json"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("kms:Decrypt failed: %s", exc)
            return None
        plaintext = bytes(resp["Plaintext"])
        _kms_dek_cache[wrapped] = (plaintext, now + _KMS_DEK_TTL)
        return plaintext


def _encrypt_metadata_blob(plaintext_json: str) -> Optional[str]:
    """Envelope-encrypt a JSON string into the ``"kms:v1:..."`` token.

    Returns None when KMS is not configured or unavailable, signalling
    the caller to store the original JSON in cleartext.
    """
    pair = _get_active_dek()
    if pair is None:
        return None
    wrapped, dek = pair
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return None
    nonce = os.urandom(12)
    try:
        ct = AESGCM(dek).encrypt(nonce, plaintext_json.encode("utf-8"), None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AES-GCM encrypt failed: %s", exc)
        return None
    import base64 as _b64
    if len(wrapped) > 0xFFFF:
        # Should never happen with KMS (typical wrapped DEK is ~150-300 bytes).
        return None
    blob = len(wrapped).to_bytes(2, "big") + wrapped + nonce + ct
    return _KMS_PREFIX + _b64.b64encode(blob).decode("ascii")


def _decrypt_metadata_blob(token: str) -> Optional[str]:
    """Reverse of :func:`_encrypt_metadata_blob`. Returns the JSON string.

    Returns None on any failure (caller should substitute a sentinel
    like ``{"_decrypt_error": true}`` so reads never raise).
    """
    if not token or not token.startswith(_KMS_PREFIX):
        return None
    import base64 as _b64
    try:
        raw = _b64.b64decode(token[len(_KMS_PREFIX):], validate=True)
    except Exception:
        return None
    if len(raw) < 2 + 12 + 16:
        return None
    wrapped_len = int.from_bytes(raw[:2], "big")
    if len(raw) < 2 + wrapped_len + 12 + 16:
        return None
    wrapped = raw[2:2 + wrapped_len]
    nonce = raw[2 + wrapped_len:2 + wrapped_len + 12]
    ct = raw[2 + wrapped_len + 12:]
    dek = _decrypt_dek(wrapped)
    if dek is None:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        pt = AESGCM(dek).decrypt(nonce, ct, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AES-GCM decrypt failed: %s", exc)
        return None
    try:
        return pt.decode("utf-8")
    except Exception:
        return None


def _decrypt_row_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate ``row`` in place so ``metadata_json`` is decrypted JSON.

    Rows written before KMS was enabled keep their cleartext JSON
    untouched. Decrypt failures yield a sentinel so reads stay
    deterministic and the caller can surface "not decryptable" to ops.
    """
    val = row.get("metadata_json")
    if isinstance(val, str) and val.startswith(_KMS_PREFIX):
        decrypted = _decrypt_metadata_blob(val)
        if decrypted is None:
            row["metadata_json"] = json.dumps(
                {"_decrypt_error": True}, separators=(",", ":")
            )
        else:
            row["metadata_json"] = decrypted
    return row


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------
#
# Two retention buckets keyed off the ``action`` column:
#
#   * SECURITY actions (key issue, SSO exchanges, admin operations) —
#     long retention so SOC 2 / LGPD investigators can reconstruct who
#     issued or used a credential. Default 365 days.
#   * OPERATIONAL actions (batch.create, tenant.branding.*, ...) — short
#     retention so we don't accumulate competitive-intel exhaust.
#     Default 90 days.
#
# Both windows are env-tunable. Use :func:`prune` from a daily job.

_SECURITY_ACTION_PREFIXES: Tuple[str, ...] = (
    "key.issue.",
    "key.revoke.",
    "key.rotate.",
    "auth.sso.",
    "auth.login.",
    "admin.",
)


def _is_security_action(action: str) -> bool:
    a = (action or "").lower()
    return any(a.startswith(p) for p in _SECURITY_ACTION_PREFIXES)

# In-memory ring (used when DB is unavailable).
_mem: Deque[Dict[str, Any]] = deque(maxlen=_MEM_MAX)
_mem_lock = threading.Lock()
_next_id = 1
_next_id_lock = threading.Lock()


def _truncate_meta(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not meta:
        return None
    try:
        s = json.dumps(meta, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001
        return None
    if len(s) > _META_MAX_BYTES:
        # Truncate but stay valid JSON: replace with a stub object.
        s = json.dumps(
            {"_truncated": True, "_orig_size": len(s)},
            separators=(",", ":"),
        )
    # Envelope-encrypt with KMS when configured. Falls through to the
    # cleartext JSON when KMS is unset or transiently unavailable so a
    # KMS outage doesn't kill audit writes.
    encrypted = _encrypt_metadata_blob(s)
    return encrypted if encrypted is not None else s


def _row(
    api_key: str,
    action: str,
    *,
    actor_email: Optional[str] = None,
    tier: Optional[str] = None,
    target: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    global _next_id
    with _next_id_lock:
        rid = _next_id
        _next_id += 1
    return {
        "id": rid,
        "ts": ts if ts is not None else time.time(),
        "api_key": api_key or "",
        "actor_email": actor_email,
        "tier": tier,
        "action": action,
        "target": target,
        "ip": ip,
        "user_agent": (user_agent or "")[:512] or None,
        "metadata_json": _truncate_meta(metadata),
    }


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

_DB_URL = os.getenv("DATABASE_URL", "")
_db_disabled = not _DB_URL


def _pg_insert(row: Dict[str, Any]) -> None:
    """Synchronous PG insert via psycopg2. Used from sync code paths.

    Must not raise — caller relies on best-effort semantics.
    """
    try:
        import psycopg2  # type: ignore
    except Exception:
        return
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                  (ts, api_key, actor_email, tier, action, target, ip,
                   user_agent, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["ts"], row["api_key"], row["actor_email"], row["tier"],
                    row["action"], row["target"], row["ip"], row["user_agent"],
                    row["metadata_json"],
                ),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_log insert failed (%s): %s", row.get("action"), exc)


def _pg_recent(api_key: str, limit: int) -> List[Dict[str, Any]]:
    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
    except Exception:
        return []
    try:
        with psycopg2.connect(_DB_URL) as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """
                SELECT id, ts, api_key, actor_email, tier, action, target,
                       ip, user_agent, metadata_json
                  FROM audit_log
                 WHERE api_key = %s
                 ORDER BY ts DESC
                 LIMIT %s
                """,
                (api_key, int(limit)),
            )
            return [_decrypt_row_metadata(dict(r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_log read failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_sync(
    api_key: str,
    action: str,
    **kwargs: Any,
) -> None:
    """Append one row synchronously. Safe to call from sync code.

    Returns immediately (does not block on the network for more than the
    single PG INSERT — typically < 5 ms on a warm pool).
    """
    row = _row(api_key, action, **kwargs)
    with _mem_lock:
        _mem.append(row)
    if not _db_disabled:
        _pg_insert(row)


async def log(
    api_key: str,
    action: str,
    **kwargs: Any,
) -> None:
    """Append one row from an async context.

    Pushes the PG INSERT to a worker thread so the request handler
    isn't blocked on IO. The in-memory ring is updated synchronously
    so unit tests see the row immediately even without a DB.
    """
    row = _row(api_key, action, **kwargs)
    with _mem_lock:
        _mem.append(row)
    if _db_disabled:
        return
    try:
        await asyncio.to_thread(_pg_insert, row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_log async insert failed: %s", exc)


def recent_for_key(api_key: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Return the most recent audit rows for a single tenant.

    Reads from PostgreSQL when configured, falls back to the in-memory
    ring otherwise. Result is ordered newest-first.
    """
    limit = max(1, min(int(limit), 1000))
    if not _db_disabled:
        rows = _pg_recent(api_key, limit)
        if rows:
            return rows
    # Fallback: scan in-memory ring.
    with _mem_lock:
        snapshot = [dict(r) for r in _mem if r["api_key"] == api_key]
    snapshot.sort(key=lambda r: r["ts"], reverse=True)
    return [_decrypt_row_metadata(r) for r in snapshot[:limit]]


def top_actors(since_ts: float, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the top API keys by audit-event count since ``since_ts``.

    Used by the admin sales overview endpoint to surface the most
    active tenants. Reads from PostgreSQL when configured, falls back
    to the in-memory ring otherwise.
    """
    limit = max(1, min(int(limit), 200))
    # Try PG first.
    if not _db_disabled:
        try:
            import psycopg2  # type: ignore
            import psycopg2.extras  # type: ignore
            with psycopg2.connect(_DB_URL) as conn, conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                cur.execute(
                    """
                    SELECT api_key, COUNT(*) AS count
                      FROM audit_log
                     WHERE ts >= %s
                     GROUP BY api_key
                     ORDER BY count DESC
                     LIMIT %s
                    """,
                    (float(since_ts), int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_log top_actors failed: %s", exc)
    # Fallback: scan in-memory ring.
    with _mem_lock:
        snapshot = [dict(r) for r in _mem if r["ts"] >= since_ts]
    counts: Dict[str, int] = {}
    for r in snapshot:
        k = r["api_key"]
        counts[k] = counts.get(k, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [{"api_key": k, "count": c} for k, c in ranked]


def prune(
    *,
    security_days: Optional[int] = None,
    operational_days: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, int]:
    """Delete audit rows older than the configured retention windows.

    Two buckets driven by :func:`_is_security_action`:

    * security actions  — kept ``security_days`` (default 365, env
      ``AUDIT_RETENTION_SECURITY_DAYS``).
    * operational actions — kept ``operational_days`` (default 90, env
      ``AUDIT_RETENTION_OPERATIONAL_DAYS``).

    Returns a dict ``{"security_deleted": int, "operational_deleted": int}``.
    Idempotent and safe to run repeatedly. Database errors are surfaced
    so a daily cron flags them — unlike the write path, silent failure
    here would let retention drift.
    """
    if security_days is None:
        security_days = int(os.getenv("AUDIT_RETENTION_SECURITY_DAYS", "365"))
    if operational_days is None:
        operational_days = int(os.getenv("AUDIT_RETENTION_OPERATIONAL_DAYS", "90"))
    if security_days < 1 or operational_days < 1:
        raise ValueError("retention windows must be ≥ 1 day")
    now_ts = float(now if now is not None else time.time())
    sec_cutoff = now_ts - security_days * 86400.0
    op_cutoff = now_ts - operational_days * 86400.0

    sec_clauses = " OR ".join(
        ["lower(action) LIKE %s"] * len(_SECURITY_ACTION_PREFIXES)
    )
    sec_params = [p + "%" for p in _SECURITY_ACTION_PREFIXES]

    deleted = {"security_deleted": 0, "operational_deleted": 0}

    if _db_disabled:
        # In-memory ring: filter by the same rules.
        with _mem_lock:
            kept = deque(maxlen=_MEM_MAX)
            for r in _mem:
                action = r.get("action") or ""
                ts = r.get("ts") or 0.0
                if _is_security_action(action):
                    if ts < sec_cutoff:
                        deleted["security_deleted"] += 1
                        continue
                else:
                    if ts < op_cutoff:
                        deleted["operational_deleted"] += 1
                        continue
                kept.append(r)
            _mem.clear()
            _mem.extend(kept)
        return deleted

    import psycopg2  # type: ignore

    with psycopg2.connect(_DB_URL) as conn, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM audit_log WHERE ts < %s AND ({sec_clauses})",
            [sec_cutoff, *sec_params],
        )
        deleted["security_deleted"] = cur.rowcount or 0
        cur.execute(
            f"DELETE FROM audit_log WHERE ts < %s AND NOT ({sec_clauses})",
            [op_cutoff, *sec_params],
        )
        deleted["operational_deleted"] = cur.rowcount or 0
        conn.commit()
    logger.info(
        "audit_log prune complete: security=%d operational=%d "
        "(security_days=%d operational_days=%d)",
        deleted["security_deleted"], deleted["operational_deleted"],
        security_days, operational_days,
    )
    return deleted


def reset_for_tests() -> None:
    """Clear the in-memory ring. Test helper only."""
    global _next_id
    with _mem_lock:
        _mem.clear()
    with _next_id_lock:
        _next_id = 1
