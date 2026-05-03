# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Distributed hop-viability cache for the repeater planner.

The bottleneck-Dijkstra inside :func:`telecom_tower_power.TelecomTowerPower.
plan_repeater_chain` evaluates O(N**2) candidate hops, and the dominant
cost per hop is :meth:`TerrainService.profile` followed by
:meth:`LinkEngine.terrain_clearance` — both purely terrain-dependent and
therefore stable over weeks/months.

This module memoises the result keyed on the geometric inputs that fully
determine the answer (rounded coordinates, antenna heights, frequency
bucket, tx power). Backends:

* **Redis** (``REDIS_URL`` set): shared across ECS tasks. TTL defaults to
  30 days because terrain doesn't change.
* **In-process LRU** (fallback): bounded at 4096 entries. Useful for
  local dev and graceful degradation if Redis is unreachable.

Public API is sync because :meth:`plan_repeater_chain` is sync and is
already called from FastAPI through ``run_in_threadpool``. Failures in
the Redis layer are logged once and the cache silently degrades to the
LRU — never break the request path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Lazy import — redis is already a transitive dep via redis.asyncio in
# repeater_jobs_store.py, so the package is available at runtime.
try:  # pragma: no cover - exercised in prod only
    import redis as _redis  # type: ignore
except Exception:  # noqa: BLE001
    _redis = None  # type: ignore

_KEY_PREFIX = "ttp:hop:v1:"
_STALE_PREFIX = "ttp:hop:v1:stale-tower:"
_DEFAULT_TTL_S = int(os.getenv("HOP_CACHE_TTL_S", str(30 * 24 * 3600)))
_STALE_TTL_S = int(os.getenv("HOP_CACHE_STALE_TTL_S", str(30 * 24 * 3600)))
_LRU_MAX = int(os.getenv("HOP_CACHE_LRU_MAX", "4096"))

# Module-level counters for observability.  Read by /metrics if desired.
_metrics_lock = threading.Lock()
_metrics: Dict[str, int] = {"hits": 0, "misses": 0, "errors": 0, "puts": 0, "stale_recomputes": 0}


def get_metrics() -> Dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def _bump(name: str, n: int = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def make_key(
    lat_a: float, lon_a: float, h_a: float,
    lat_b: float, lon_b: float, h_b: float,
    f_hz: float, power_dbm: float = 43.0,
) -> str:
    """Build a stable, symmetric cache key for hop (A,B).

    Coordinates are rounded to 4 decimals (~11 m) and heights to 1 m;
    frequency is bucketed to 10 MHz. The (a, b) tuple is sorted so that
    plan_repeater_chain's a→b and b→a lookups hit the same entry —
    terrain clearance + FSPL are symmetric in the inputs we cache.
    """
    end_a = (round(lat_a, 4), round(lon_a, 4), round(h_a, 0))
    end_b = (round(lat_b, 4), round(lon_b, 4), round(h_b, 0))
    lo, hi = (end_a, end_b) if end_a <= end_b else (end_b, end_a)
    f_bucket = round(f_hz / 1e7) * 10  # MHz, bucketed to 10 MHz
    p_bucket = round(power_dbm, 0)
    raw = json.dumps([lo, hi, f_bucket, p_bucket], separators=(",", ":"))
    return _KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _LRU:
    """Tiny thread-safe LRU. Stores already-encoded values."""

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._d: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, k: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            v = self._d.get(k)
            if v is None:
                return None
            self._d.move_to_end(k)
            return v

    def put(self, k: str, v: Dict[str, Any]) -> None:
        with self._lock:
            self._d[k] = v
            self._d.move_to_end(k)
            while len(self._d) > self._maxsize:
                self._d.popitem(last=False)


class _RedisBackend:
    name = "redis"

    def __init__(self, url: str) -> None:
        # decode_responses=True: store JSON strings, get them back as str.
        # socket_timeout keeps a slow Redis from gating the whole planner.
        self._client = _redis.Redis.from_url(  # type: ignore[union-attr]
            url, decode_responses=True,
            socket_timeout=0.25, socket_connect_timeout=0.5,
        )
        self._ttl = _DEFAULT_TTL_S
        self._broken = False

    def get(self, k: str) -> Optional[Dict[str, Any]]:
        if self._broken:
            return None
        try:
            raw = self._client.get(k)
        except Exception as e:  # noqa: BLE001
            self._broken = True
            _bump("errors")
            logger.warning("hop_cache: Redis GET failed (%s); degrading to LRU", e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def put(self, k: str, v: Dict[str, Any]) -> None:
        if self._broken:
            return
        try:
            self._client.set(k, json.dumps(v, separators=(",", ":")), ex=self._ttl)
        except Exception as e:  # noqa: BLE001
            self._broken = True
            _bump("errors")
            logger.warning("hop_cache: Redis SET failed (%s); degrading to LRU", e)


class _MemoryBackend:
    name = "memory"

    def __init__(self) -> None:
        self._lru = _LRU(_LRU_MAX)

    def get(self, k: str) -> Optional[Dict[str, Any]]:
        return self._lru.get(k)

    def put(self, k: str, v: Dict[str, Any]) -> None:
        self._lru.put(k, v)


# ---------------------------------------------------------------------------
# Singleton + always-on local LRU as last-resort fallback
# ---------------------------------------------------------------------------

_BACKEND: Optional[Any] = None
_LOCAL_LRU = _LRU(_LRU_MAX)
_init_lock = threading.Lock()


def _get_backend() -> Any:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    with _init_lock:
        if _BACKEND is not None:
            return _BACKEND
        url = os.getenv("HOP_CACHE_REDIS_URL") or os.getenv("REDIS_URL")
        if url and _redis is not None:
            try:
                _BACKEND = _RedisBackend(url)
                logger.info("hop_cache: Redis backend initialized (ttl=%ds)", _DEFAULT_TTL_S)
            except Exception as e:  # noqa: BLE001
                logger.warning("hop_cache: Redis init failed (%s); using in-memory LRU", e)
                _BACKEND = _MemoryBackend()
        else:
            _BACKEND = _MemoryBackend()
            logger.info("hop_cache: in-memory LRU backend (no REDIS_URL)")
        return _BACKEND


# ---------------------------------------------------------------------------
# Public sync API
# ---------------------------------------------------------------------------

def get_or_compute(
    key: str,
    compute: Callable[[], Tuple[float, bool]],
    *,
    tower_ids: Sequence[str] = (),
) -> Tuple[float, bool]:
    """Return cached (cost_db, feasible) for *key*, or run *compute*.

    *compute* is invoked at most once per cache miss. Its result MUST be a
    tuple ``(cost_db: float, feasible: bool)``. The feasible flag is what
    the planner uses to prune impossible edges; we store both so callers
    can also reason about edge weight without recomputing.

    *tower_ids*: when provided, any tower flagged stale (e.g. by the
    satellite-change robot via ``mark_towers_stale``) forces a recompute
    and refreshes the cached entry. After the refresh the stale markers
    for those towers are cleared so subsequent calls hit cache again.
    """
    backend = _get_backend()

    forced_recompute = False
    stale_hits: list[str] = []
    if tower_ids:
        for tid in tower_ids:
            if tid and is_tower_stale(tid):
                forced_recompute = True
                stale_hits.append(tid)

    # Tier 1: shared backend (Redis when configured).
    if not forced_recompute:
        cached = backend.get(key)
        if cached is not None:
            _bump("hits")
            return float(cached["c"]), bool(cached["f"])

        # Tier 2: process-local LRU (covers brief Redis blips and cold workers).
        cached = _LOCAL_LRU.get(key)
        if cached is not None:
            _bump("hits")
            # Best-effort: warm the shared backend with what we already had.
            backend.put(key, cached)
            return float(cached["c"]), bool(cached["f"])

    if forced_recompute:
        _bump("stale_recomputes")
    else:
        _bump("misses")
    cost_db, feasible = compute()
    payload = {"c": float(cost_db), "f": bool(feasible)}
    _LOCAL_LRU.put(key, payload)
    backend.put(key, payload)
    _bump("puts")
    if stale_hits:
        _clear_stale_towers(stale_hits)
    return cost_db, feasible


# ---------------------------------------------------------------------------
# Stale-tower markers (closed-loop with satellite-change robot)
# ---------------------------------------------------------------------------

def _stale_key(tower_id: str) -> str:
    return _STALE_PREFIX + tower_id


def mark_towers_stale(
    tower_ids: Iterable[str],
    *,
    ttl_s: Optional[int] = None,
    reason: str = "satellite-change",
) -> int:
    """Mark *tower_ids* so the next planner call recomputes their hops.

    Used by ``scripts/invalidate_rf_cache.py`` after the satellite-change
    robot detects fresh imagery over a tower. Returns the number of
    markers actually written. Falls back to a no-op when the backend has
    no native expiring write (in-process LRU) — cache invalidation is
    only meaningful with a shared Redis.
    """
    backend = _get_backend()
    ttl = int(ttl_s) if ttl_s is not None else _STALE_TTL_S
    written = 0
    if not isinstance(backend, _RedisBackend):
        # In-memory backend has no cross-process visibility — short-circuit.
        logger.info("hop_cache.mark_towers_stale: backend=memory; skipping")
        return 0
    client = backend._client  # noqa: SLF001
    for tid in tower_ids:
        if not tid:
            continue
        try:
            client.set(_stale_key(tid), reason, ex=ttl)
            written += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("hop_cache: failed to mark %s stale: %s", tid, e)
    return written


def is_tower_stale(tower_id: str) -> bool:
    """Return True if *tower_id* has an active stale marker in Redis."""
    backend = _get_backend()
    if not isinstance(backend, _RedisBackend):
        return False
    try:
        return backend._client.exists(_stale_key(tower_id)) > 0  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False


def _clear_stale_towers(tower_ids: Iterable[str]) -> None:
    backend = _get_backend()
    if not isinstance(backend, _RedisBackend):
        return
    try:
        keys = [_stale_key(t) for t in tower_ids if t]
        if keys:
            backend._client.delete(*keys)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def reset_for_tests() -> None:
    """Drop all in-process state. Tests only — not for production use."""
    global _BACKEND
    with _init_lock:
        _BACKEND = None
    _LOCAL_LRU._d.clear()  # noqa: SLF001
    with _metrics_lock:
        for k in _metrics:
            _metrics[k] = 0
