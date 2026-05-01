# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Backend abstraction for the /plan_repeater async job state.

Picks a Redis-backed store when REDIS_URL is set (production / multi-task
ECS deployments) and falls back to an in-process dict + asyncio.Lock for
local dev or single-task setups.

Public async API:
    await store.create(job_id, payload)
    await store.update(job_id, **fields)        # partial merge
    await store.get(job_id)                     # -> dict | None
    await store.reap(ttl_s, max_jobs)           # housekeeping (no-op on Redis)

Each job is a small JSON dict (job_id, status, owner, tower_id, …, result).
The Redis backend stores it under  repeater:job:<id>  with EXPIRE=ttl_s
so completed jobs disappear automatically; an auxiliary ZSET tracks
creation time so the max-jobs cap can evict the oldest entries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover – optional at import time
    import redis.asyncio as aredis  # type: ignore
except Exception:  # noqa: BLE001
    aredis = None  # type: ignore

_KEY_PREFIX = "repeater:job:"
_INDEX_ZSET = "repeater:jobs:index"


class _MemoryBackend:
    backend = "memory"

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, job_id: str, payload: Dict[str, Any]) -> None:
        async with self._lock:
            self._jobs[job_id] = dict(payload)

    async def update(self, job_id: str, **fields: Any) -> None:
        async with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j is not None else None

    async def reap(self, ttl_s: int, max_jobs: int) -> None:
        now = time.time()
        async with self._lock:
            stale = [
                jid for jid, j in self._jobs.items()
                if j.get("status") in ("done", "error")
                and (now - j.get("finished_at", now)) > ttl_s
            ]
            for jid in stale:
                self._jobs.pop(jid, None)
            if len(self._jobs) > max_jobs:
                oldest = sorted(
                    self._jobs.items(),
                    key=lambda kv: kv[1].get("created_at", 0),
                )[: len(self._jobs) - max_jobs]
                for jid, _ in oldest:
                    self._jobs.pop(jid, None)


class _RedisBackend:
    backend = "redis"

    def __init__(self, url: str) -> None:
        # decode_responses=True so we get str back from Redis (JSON loads OK).
        self._url = url
        self._client = aredis.Redis.from_url(url, decode_responses=True)
        # Default TTL for the per-job key. Refreshed on every update so a
        # long-running job doesn't expire mid-flight.
        self._ttl_s = int(os.getenv("REPEATER_JOBS_TTL_S", "900"))

    @staticmethod
    def _k(job_id: str) -> str:
        return _KEY_PREFIX + job_id

    async def create(self, job_id: str, payload: Dict[str, Any]) -> None:
        pipe = self._client.pipeline()
        pipe.set(self._k(job_id), json.dumps(payload), ex=self._ttl_s)
        # Index by created_at for the max-jobs cap eviction below.
        pipe.zadd(_INDEX_ZSET, {job_id: float(payload.get("created_at", time.time()))})
        await pipe.execute()

    async def update(self, job_id: str, **fields: Any) -> None:
        # Read-modify-write. Race with concurrent updates is acceptable here:
        # the only writers for a given job_id are create() and the single
        # background task that finalises it, so contention is minimal.
        raw = await self._client.get(self._k(job_id))
        if raw is None:
            logger.warning("repeater job %s vanished before update", job_id)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("repeater job %s payload corrupt, dropping", job_id)
            await self._client.delete(self._k(job_id))
            await self._client.zrem(_INDEX_ZSET, job_id)
            return
        data.update(fields)
        # Refresh TTL so a long task doesn't lose its result by expiring
        # exactly when the user is about to poll.
        await self._client.set(self._k(job_id), json.dumps(data), ex=self._ttl_s)

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._client.get(self._k(job_id))
        if raw is None:
            # Lazy-clean any stale index entry.
            await self._client.zrem(_INDEX_ZSET, job_id)
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def reap(self, ttl_s: int, max_jobs: int) -> None:
        # Per-job EXPIRE handles TTL eviction. We only need to enforce the
        # global max_jobs cap here.
        size = await self._client.zcard(_INDEX_ZSET)
        if size <= max_jobs:
            return
        # Drop the oldest (size - max_jobs) entries.
        excess = size - max_jobs
        oldest = await self._client.zrange(_INDEX_ZSET, 0, excess - 1)
        if not oldest:
            return
        pipe = self._client.pipeline()
        for jid in oldest:
            pipe.delete(self._k(jid))
            pipe.zrem(_INDEX_ZSET, jid)
        await pipe.execute()


_BACKEND: Optional[Any] = None


def get_store() -> Any:
    """Return the singleton job store, picking the backend on first call."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    url = os.getenv("REDIS_URL")
    if url and aredis is not None:
        try:
            _BACKEND = _RedisBackend(url)
            logger.info("repeater_jobs_store: using Redis backend (%s)", url.split("@")[-1])
            return _BACKEND
        except Exception as exc:  # noqa: BLE001
            logger.warning("repeater_jobs_store: Redis init failed (%s); falling back to memory", exc)
    _BACKEND = _MemoryBackend()
    logger.info("repeater_jobs_store: using in-memory backend")
    return _BACKEND
