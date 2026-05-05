# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""webhook_store.py — Enterprise outbound webhook registry + dispatcher.

Persists webhook subscriptions to either:
- **Postgres** when ``DATABASE_URL`` is set and ``psycopg2`` is importable
  (production: survives restarts, shared across N tasks). Schema lives in
  the ``webhooks`` table created by alembic revision ``f8b2d1e09a47``.
- **JSON file** otherwise — ``WEBHOOK_STORE_PATH`` (default
  ``./webhook_store.json``). Used in dev / tests / SQLite mode.

Public API (unchanged): ``register``, ``list_all``, ``get``, ``delete``,
``update_enabled``, ``valid_events``, ``dispatch``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

try:
    import psycopg2  # type: ignore[import-untyped]
    import psycopg2.extras  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]

logger = logging.getLogger("webhook_store")

_STORE_PATH = Path(os.getenv("WEBHOOK_STORE_PATH", "./webhook_store.json"))
_DELIVERY_TIMEOUT = float(os.getenv("WEBHOOK_DELIVERY_TIMEOUT", "5.0"))
_DELIVERY_RETRIES = int(os.getenv("WEBHOOK_DELIVERY_RETRIES", "1"))
_ALLOW_PRIVATE = os.getenv("WEBHOOK_ALLOW_PRIVATE", "false").lower() in ("1", "true", "yes")
_USER_AGENT = "TelecomTowerPower-Webhooks/1.0"

_RAW_DATABASE_URL = os.getenv("DATABASE_URL")
_DATABASE_URL = _RAW_DATABASE_URL
if _DATABASE_URL:
    _DATABASE_URL = _DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    if _DATABASE_URL.startswith("postgres://"):
        _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)

_USE_PG = (
    bool(_DATABASE_URL)
    and psycopg2 is not None
    and not (_DATABASE_URL or "").startswith("sqlite")
)


_VALID_EVENTS = {
    "internal.upload.completed",
    "anatel.validation.completed",
    "anatel.validation.completed.pdf",
}


def valid_events() -> List[str]:
    return sorted(_VALID_EVENTS)


# ─────────────────────────────────────────────────────────────────────
# URL safety / SSRF guard
# ─────────────────────────────────────────────────────────────────────


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("webhook url must be http(s)")
    if not parsed.hostname:
        raise ValueError("webhook url must include a hostname")
    if parsed.scheme == "http" and not _ALLOW_PRIVATE:
        raise ValueError("webhook url must use https")
    if not _ALLOW_PRIVATE:
        host = parsed.hostname
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError(f"webhook url host could not be resolved: {exc}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            ):
                raise ValueError(
                    f"webhook url resolves to non-public address {ip} "
                    "(set WEBHOOK_ALLOW_PRIVATE=true to permit)"
                )


# ─────────────────────────────────────────────────────────────────────
# Backends
# ─────────────────────────────────────────────────────────────────────


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
        created_at = row["created_at"]
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        return {
            "id": row["id"],
            "url": row["url"],
            "events": list(row["events"] or []),
            "secret": row["secret"],
            "enabled": bool(row["enabled"]),
            "created_at": created_at,
            "created_by": row["created_by"],
            "description": row.get("description") or "",
        }

    def insert(self, rec: Dict[str, Any]) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO webhooks
                  (id, url, events, secret, enabled, created_at, created_by, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rec["id"], rec["url"], rec["events"], rec["secret"],
                    rec["enabled"], rec["created_at"], rec["created_by"],
                    rec.get("description") or "",
                ),
            )
            conn.commit()

    def list_all(self) -> List[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM webhooks ORDER BY created_at ASC")
            return [self._row_to_record(r) for r in cur.fetchall()]

    def get(self, webhook_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM webhooks WHERE id = %s", (webhook_id,))
            row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def delete(self, webhook_id: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM webhooks WHERE id = %s", (webhook_id,))
            deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def update_enabled(self, webhook_id: str, enabled: bool) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE webhooks SET enabled = %s WHERE id = %s",
                (bool(enabled), webhook_id),
            )
            updated = cur.rowcount > 0
            conn.commit()
        return updated

    def list_for_event(self, event: str) -> List[Dict[str, Any]]:
        with self._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM webhooks WHERE enabled = TRUE AND %s = ANY(events)",
                (event,),
            )
            return [self._row_to_record(r) for r in cur.fetchall()]


class _JsonBackend:
    backend = "json"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            logger.exception("webhook_store: failed to load %s", self.path)
            return {}

    def _save(self, records: Dict[str, Dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with self._lock:
            try:
                with tmp.open("w", encoding="utf-8") as fh:
                    json.dump(records, fh, indent=2, sort_keys=True)
                os.replace(tmp, self.path)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    def insert(self, rec: Dict[str, Any]) -> None:
        records = self._load()
        records[rec["id"]] = rec
        self._save(records)

    def list_all(self) -> List[Dict[str, Any]]:
        out = list(self._load().values())
        out.sort(key=lambda r: r.get("created_at") or "")
        return out

    def get(self, webhook_id: str) -> Optional[Dict[str, Any]]:
        return self._load().get(webhook_id)

    def delete(self, webhook_id: str) -> bool:
        records = self._load()
        if webhook_id not in records:
            return False
        records.pop(webhook_id, None)
        self._save(records)
        return True

    def update_enabled(self, webhook_id: str, enabled: bool) -> bool:
        records = self._load()
        rec = records.get(webhook_id)
        if not rec:
            return False
        rec["enabled"] = bool(enabled)
        records[webhook_id] = rec
        self._save(records)
        return True

    def list_for_event(self, event: str) -> List[Dict[str, Any]]:
        return [
            r for r in self._load().values()
            if r.get("enabled") and event in (r.get("events") or [])
        ]


def _build_backend():
    if _USE_PG:
        try:
            be = _PgBackend(_DATABASE_URL)  # type: ignore[arg-type]
            with be._conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM webhooks LIMIT 1")
            logger.info("webhook_store: postgres backend (table=webhooks)")
            return be
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "webhook_store: postgres unavailable (%s); falling back to JSON %s",
                exc, _STORE_PATH,
            )
    logger.info("webhook_store: json backend (path=%s)", _STORE_PATH)
    return _JsonBackend(_STORE_PATH)


_backend = _build_backend()


def backend_name() -> str:
    return _backend.backend


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def register(
    *,
    url: str,
    events: Iterable[str],
    created_by: str,
    secret: Optional[str] = None,
    description: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    _validate_url(url)
    ev_list = sorted({e.strip() for e in events if e and e.strip()})
    if not ev_list:
        raise ValueError("at least one event is required")
    unknown = [e for e in ev_list if e not in _VALID_EVENTS]
    if unknown:
        raise ValueError(f"unknown event(s): {unknown}")

    secret = secret or secrets.token_hex(32)
    if len(secret) < 16:
        raise ValueError("webhook secret too short (min 16 chars)")

    record = {
        "id": uuid.uuid4().hex,
        "url": url,
        "events": ev_list,
        "secret": secret,
        "enabled": bool(enabled),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": created_by,
        "description": (description or "")[:200],
    }
    _backend.insert(record)
    return record


def list_all(*, redact_secret: bool = True) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in _backend.list_all():
        item = dict(rec)
        if redact_secret:
            sec = item.get("secret") or ""
            item["secret"] = f"***{sec[-4:]}" if sec else ""
        out.append(item)
    return out


def get(webhook_id: str) -> Optional[Dict[str, Any]]:
    return _backend.get(webhook_id)


def delete(webhook_id: str) -> bool:
    return _backend.delete(webhook_id)


def update_enabled(webhook_id: str, enabled: bool) -> bool:
    return _backend.update_enabled(webhook_id, enabled)


# ─────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(timestamp.encode("ascii"))
    mac.update(b".")
    mac.update(body)
    return f"sha256={mac.hexdigest()}"


async def _deliver_one(
    rec: Dict[str, Any],
    event: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Deliver a single payload to one subscription. Never raises."""
    import httpx  # local import: keep module importable without httpx

    body_dict = {
        "event": event,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    body = json.dumps(body_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ts = str(int(time.time()))
    delivery_id = uuid.uuid4().hex
    sig = _sign(rec["secret"], ts, body)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
        "X-TTP-Event": event,
        "X-TTP-Webhook-Id": rec["id"],
        "X-TTP-Delivery": delivery_id,
        "X-TTP-Timestamp": ts,
        "X-TTP-Signature": sig,
    }

    last_status: Optional[int] = None
    last_error: Optional[str] = None
    attempts = 0
    for attempt in range(_DELIVERY_RETRIES + 1):
        attempts = attempt + 1
        try:
            async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT) as client:
                resp = await client.post(rec["url"], content=body, headers=headers)
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    return {
                        "id": rec["id"], "delivery_id": delivery_id,
                        "ok": True, "status": resp.status_code, "attempts": attempts,
                    }
                last_error = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < _DELIVERY_RETRIES:
            await asyncio.sleep(2 ** attempt)
    logger.warning(
        "webhook delivery failed id=%s url=%s event=%s attempts=%d last=%s",
        rec.get("id"), rec.get("url"), event, attempts, last_error,
    )
    return {
        "id": rec["id"], "delivery_id": delivery_id,
        "ok": False, "status": last_status, "attempts": attempts, "error": last_error,
    }


async def dispatch(event: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deliver ``event`` + ``payload`` to all enabled subscriptions matching it.

    Safe to call from a fire-and-forget task; never raises.
    """
    try:
        records = _backend.list_for_event(event)
    except Exception:  # noqa: BLE001
        logger.exception("webhook dispatch: failed to load store")
        return []
    if not records:
        return []
    return await asyncio.gather(*[_deliver_one(r, event, payload) for r in records])
