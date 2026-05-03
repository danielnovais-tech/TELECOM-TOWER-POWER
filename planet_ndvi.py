# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Cache-backed NDVI-delta extractor for Sionna feature schema v2.

The Sionna feature builder consumes a single scalar in ``[-1, +1]``
("NDVI delta") that captures recent vegetation change at a point —
positive = greening (e.g. wet-season regrowth), negative = browning /
clearing (e.g. fire scar, dry-season dieback). The model is supposed
to learn that browning correlates with reduced foliage attenuation
relative to MapBiomas's static class label.

This module deliberately does NOT fetch Planet imagery rasters at
runtime — that would be too slow and expensive on the propagation
hot-path. Instead the operator runs ``scripts/refresh_ndvi_cache.py``
periodically (e.g. monthly) to populate a JSON cache, and runtime
lookups are O(1) dictionary hits.

Cache file layout
-----------------
``planet_ndvi_cache.json`` (path overridable via ``PLANET_NDVI_CACHE``)::

    {
        "schema": "ndvi-delta-v1",
        "generated_at": "2026-05-15T03:14:22Z",
        "resolution_deg": 0.05,
        "cells": {
            "-15.70,-47.90": 0.12,
            "-15.75,-47.95": -0.04
        }
    }

Keys are ``f"{lat:.2f},{lon:.2f}"`` rounded to ``resolution_deg``
(default 0.05° ≈ 5 km). A missing key returns ``None`` and the
feature builder sets the missing flag — so the model can learn an
"unknown morphology change" residual rather than treating zero as
a real measurement.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_CACHE_SCHEMA = "ndvi-delta-v1"
_DEFAULT_RESOLUTION_DEG = 0.05


def _default_cache_path() -> str:
    explicit = os.getenv("PLANET_NDVI_CACHE")
    if explicit:
        return explicit
    # Co-located with the rest of the small JSON caches at the repo root.
    return str(Path(__file__).resolve().parent / "planet_ndvi_cache.json")


def _quantise(value: float, resolution_deg: float) -> float:
    """Round ``value`` to the nearest multiple of ``resolution_deg``.

    We use ``round()`` rather than ``floor()`` so a query a few metres
    north of a cell boundary doesn't switch buckets — the inferred
    NDVI delta is locally smooth at this scale.
    """
    if resolution_deg <= 0:
        return value
    return round(value / resolution_deg) * resolution_deg


def _key(lat: float, lon: float, resolution_deg: float) -> str:
    qlat = _quantise(lat, resolution_deg)
    qlon = _quantise(lon, resolution_deg)
    return f"{qlat:.2f},{qlon:.2f}"


class PlanetNdviExtractor:
    """Process-wide cache for NDVI-delta lookups.

    The extractor is intentionally tolerant: any I/O or parse failure
    degrades to "no entries" rather than raising, because the feature
    builder's contract is that a missing value silently sets the
    missing flag. Hard failures here would propagate up and break
    inference for the entire request.
    """

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._path = cache_path or _default_cache_path()
        self._cells: Dict[str, float] = {}
        self._resolution = _DEFAULT_RESOLUTION_DEG
        self._loaded = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:  # double-checked under lock
                return
            self._loaded = True  # mark loaded even on failure → no busy retries
            p = Path(self._path)
            if not p.is_file():
                logger.info("planet_ndvi: no cache at %s — all lookups will be missing", p)
                return
            try:
                raw = json.loads(p.read_text())
            except Exception:
                logger.exception("planet_ndvi: cache %s unreadable; treating as empty", p)
                return
            schema = raw.get("schema")
            if schema != _CACHE_SCHEMA:
                logger.warning(
                    "planet_ndvi: cache schema %s != %s; ignoring",
                    schema, _CACHE_SCHEMA,
                )
                return
            res = raw.get("resolution_deg")
            if isinstance(res, (int, float)) and res > 0:
                self._resolution = float(res)
            cells = raw.get("cells", {})
            if not isinstance(cells, dict):
                logger.warning("planet_ndvi: cells is not a dict; ignoring")
                return
            # Only keep finite floats in [-1, +1] — anything else is corruption.
            kept: Dict[str, float] = {}
            for k, v in cells.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv) or fv < -1.0 or fv > 1.0:
                    continue
                kept[k] = fv
            self._cells = kept
            logger.info(
                "planet_ndvi: loaded %d cells from %s (res=%.3f°)",
                len(kept), p, self._resolution,
            )

    def get_ndvi_delta(self, lat: float, lon: float) -> Optional[float]:
        """Return the NDVI delta in ``[-1, +1]`` or ``None`` if uncached."""
        self._ensure_loaded()
        if not self._cells:
            return None
        return self._cells.get(_key(lat, lon, self._resolution))

    @property
    def resolution_deg(self) -> float:
        self._ensure_loaded()
        return self._resolution


_singleton: Optional[PlanetNdviExtractor] = None
_singleton_lock = threading.Lock()


def get_extractor() -> PlanetNdviExtractor:
    """Return the process-wide :class:`PlanetNdviExtractor` singleton."""
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PlanetNdviExtractor()
    return _singleton


def reset_for_tests() -> None:
    """Drop the singleton — only used by unit tests after monkeypatching env."""
    global _singleton
    with _singleton_lock:
        _singleton = None


__all__ = [
    "PlanetNdviExtractor",
    "get_extractor",
    "reset_for_tests",
]
