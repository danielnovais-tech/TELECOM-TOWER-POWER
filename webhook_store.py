# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""webhook_store.py — Enterprise outbound webhook registry + dispatcher.

Persists webhook subscriptions to a JSON file (``WEBHOOK_STORE_PATH``,
default ``./webhook_store.json``) and delivers signed events to every
matching subscription. Designed to be drop-in for the Tier-1 admin
endpoints — no DB required.

Each subscription record:

    {
      "id": "<uuid4-hex>",
      "url": "https://example.com/hook",
      "events": ["internal.upload.completed", "anatel.validation.completed"],
      "secret": "<random-32-byte-hex>",
      "enabled": true,
      "created_at": "<iso8601>",
      "created_by": "<actor email or admin key id>",
      "description": "free text, optional",
    }

Delivery:
- POST JSON body to ``url`` with these headers:
    Content-Type: application/json
    User-Agent: TelecomTowerPower-Webhooks/1.0
    X-TTP-Event: <event name>
    X-TTP-Webhook-Id: <subscription id>
    X-TTP-Delivery: <uuid4-hex per attempt>
    X-TTP-Timestamp: <unix seconds>
    X-TTP-Signature: sha256=<hex hmac of "<timestamp>.<body>" with secret>
- Timeout: ``WEBHOOK_DELIVERY_TIMEOUT`` seconds (default 5).
- Retries: ``WEBHOOK_DELIVERY_RETRIES`` extra attempts (default 1) with
  exponential backoff (1s, 2s, ...).
- Disallows internal/loopback/link-local destinations unless
  ``WEBHOOK_ALLOW_PRIVATE=true`` (SSRF guard for default deploy).

The dispatcher is async and intended to be fired from request handlers
via ``asyncio.create_task(dispatch(event, payload))`` so HTTP latency
is unaffected by webhook receivers.
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

logger = logging.getLogger("webhook_store")

_STORE_PATH = Path(os.getenv("WEBHOOK_STORE_PATH", "./webhook_store.json"))
_DELIVERY_TIMEOUT = float(os.getenv("WEBHOOK_DELIVERY_TIMEOUT", "5.0"))
_DELIVERY_RETRIES = int(os.getenv("WEBHOOK_DELIVERY_RETRIES", "1"))
_ALLOW_PRIVATE = os.getenv("WEBHOOK_ALLOW_PRIVATE", "false").lower() in ("1", "true", "yes")
_USER_AGENT = "TelecomTowerPower-Webhooks/1.0"

_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────


def _load() -> Dict[str, Dict[str, Any]]:
    if not _STORE_PATH.exists():
        return {}
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        logger.exception("webhook_store: failed to load %s", _STORE_PATH)
        return {}


def _save(records: Dict[str, Dict[str, Any]]) -> None:
    tmp = _STORE_PATH.with_suffix(_STORE_PATH.suffix + ".tmp")
    with _lock:
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2, sort_keys=True)
            os.replace(tmp, _STORE_PATH)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────


_VALID_EVENTS = {
    "internal.upload.completed",
    "anatel.validation.completed",
    "anatel.validation.completed.pdf",
}


def valid_events() -> List[str]:
    return sorted(_VALID_EVENTS)


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("webhook url must be http(s)")
    if not parsed.hostname:
        raise ValueError("webhook url must include a hostname")
    if parsed.scheme == "http" and not _ALLOW_PRIVATE:
        # http permitted only when private hosts are explicitly allowed
        # (typically test/dev). In production all webhooks are TLS.
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
    records = _load()
    records[record["id"]] = record
    _save(records)
    return record


def list_all(*, redact_secret: bool = True) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in _load().values():
        item = dict(rec)
        if redact_secret:
            sec = item.get("secret") or ""
            item["secret"] = f"***{sec[-4:]}" if sec else ""
        out.append(item)
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def get(webhook_id: str) -> Optional[Dict[str, Any]]:
    return _load().get(webhook_id)


def delete(webhook_id: str) -> bool:
    records = _load()
    if webhook_id not in records:
        return False
    records.pop(webhook_id, None)
    _save(records)
    return True


def update_enabled(webhook_id: str, enabled: bool) -> bool:
    records = _load()
    rec = records.get(webhook_id)
    if not rec:
        return False
    rec["enabled"] = bool(enabled)
    records[webhook_id] = rec
    _save(records)
    return True


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
                        "id": rec["id"],
                        "delivery_id": delivery_id,
                        "ok": True,
                        "status": resp.status_code,
                        "attempts": attempts,
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
        "id": rec["id"],
        "delivery_id": delivery_id,
        "ok": False,
        "status": last_status,
        "attempts": attempts,
        "error": last_error,
    }


async def dispatch(event: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deliver ``event`` + ``payload`` to all enabled subscriptions matching it.

    Safe to call from a fire-and-forget task; never raises.
    """
    try:
        records = [
            r for r in _load().values()
            if r.get("enabled") and event in (r.get("events") or [])
        ]
    except Exception:  # noqa: BLE001
        logger.exception("webhook dispatch: failed to load store")
        return []
    if not records:
        return []
    return await asyncio.gather(*[_deliver_one(r, event, payload) for r in records])
