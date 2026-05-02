# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""ITU-R P.1812 wrapper around eeveetza/Py1812.

Heavy native dependency (numpy + ITU digital maps `P1812.npz` derived
from `N050.TXT` / `DN50.TXT`). The maps cannot be redistributed by us
(ITU license), so the container provisions them at boot from S3 — same
pattern as MapBiomas. When the maps or the ``Py1812`` package are
absent, ``predict_basic_loss()`` returns ``None`` and the caller falls
back to the ridge / physics path.

Public API:
    is_available()   -> bool
    predict_basic_loss(...) -> Optional[float]   # dB

Caching (mirror of ``mapbiomas_clutter`` / ``hop_cache``):
    in-process LRU(2048) → Redis 7 d TTL → P1812.bt_loss().

Cache key includes a SHA-1 of (rounded coords, freq, antenna heights,
terrain profile bytes, polarisation, time/location percentages). A
single ``P1812.bt_loss`` call on a 500-point profile takes ~30-80 ms,
so the cache is essential for the heat-map (``predict_grid``) path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env)
# ---------------------------------------------------------------------------

# Disable knob — set ``ITU_P1812_DISABLED=1`` to short-circuit the
# wrapper without touching ``Py1812``. Useful for incident response
# (e.g. pin to the ridge model only).
_DISABLED = os.environ.get("ITU_P1812_DISABLED", "").lower() in {"1", "true", "yes"}

# Default antenna polarisation: cellular = vertical (2). DTT = horizontal (1).
_POL = int(os.environ.get("ITU_P1812_POL", "2"))

# Default radiometeorological zone: Brazil overland → "Inland" = 4.
# Coastal links should override per-call (tx near sea + rx inland).
_ZONE_DEFAULT = int(os.environ.get("ITU_P1812_ZONE_DEFAULT", "4"))

# Default time percentage (median, 50%) and location percentage.
_TIME_PCT = float(os.environ.get("ITU_P1812_TIME_PCT", "50"))
_LOC_PCT = float(os.environ.get("ITU_P1812_LOC_PCT", "50"))

# Redis cache TTL: 7 days (terrain rarely changes; freq/antenna identical
# requests dominate in heat-map workloads).
_REDIS_TTL_SEC = int(os.environ.get("ITU_P1812_REDIS_TTL_SEC", str(7 * 86400)))
_REDIS_URL = (
    os.environ.get("ITU_P1812_REDIS_URL")
    or os.environ.get("REDIS_URL")
    or ""
)
_REDIS_KEY_PREFIX = "itu1812:"

# Coordinate rounding (decimal places). 4 ≈ 11 m at the equator.
_COORD_ROUND = 4

# ---------------------------------------------------------------------------
# Lazy import + availability check
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_p1812_module: Any = None
_p1812_import_failed = False


def _try_import_p1812() -> Any:
    """Return the ``Py1812.P1812`` module or ``None`` if unavailable."""
    global _p1812_module, _p1812_import_failed
    if _p1812_module is not None:
        return _p1812_module
    if _p1812_import_failed or _DISABLED:
        return None
    with _lock:
        if _p1812_module is not None:
            return _p1812_module
        try:
            from Py1812 import P1812  # type: ignore[import-not-found]
            _p1812_module = P1812
            logger.info("Py1812 loaded; ITU-R P.1812 propagation model available")
            return P1812
        except Exception as exc:  # noqa: BLE001
            _p1812_import_failed = True
            logger.info(
                "Py1812 not available (%s) — ITU P.1812 disabled, ridge model "
                "and physics fallback will be used",
                exc,
            )
            return None


def is_available() -> bool:
    """True iff Py1812 is importable and not explicitly disabled."""
    return _try_import_p1812() is not None


# ---------------------------------------------------------------------------
# Redis cache plumbing (best-effort, never raises)
# ---------------------------------------------------------------------------

_redis_client: Any = None
_redis_init_failed = False


def _get_redis() -> Any:
    global _redis_client, _redis_init_failed
    if _redis_client is not None or _redis_init_failed or not _REDIS_URL:
        return _redis_client
    with _lock:
        if _redis_client is not None or _redis_init_failed:
            return _redis_client
        try:
            import redis  # type: ignore[import-not-found]
            client = redis.from_url(_REDIS_URL, socket_timeout=0.5)
            client.ping()
            _redis_client = client
            logger.info("ITU P.1812 Redis cache enabled at %s", _REDIS_URL)
            return client
        except Exception as exc:  # noqa: BLE001
            _redis_init_failed = True
            logger.info("ITU P.1812 Redis cache disabled (%s); falling back to LRU", exc)
            return None


# ---------------------------------------------------------------------------
# Cache-key derivation
# ---------------------------------------------------------------------------

def _profile_digest(d_km: Sequence[float], h_m: Sequence[float]) -> str:
    """SHA-1 of the terrain profile (rounded to 1 m / 10 m)."""
    d = np.asarray(d_km, dtype=np.float64).round(3)
    h = np.asarray(h_m, dtype=np.float64).round(0)
    raw = d.tobytes() + b"|" + h.tobytes()
    return hashlib.sha1(raw).hexdigest()[:16]


def _build_key(
    *,
    f_ghz: float,
    d_km: Sequence[float],
    h_m: Sequence[float],
    htg: float,
    hrg: float,
    phi_t: float,
    lam_t: float,
    phi_r: float,
    lam_r: float,
    pol: int,
    zone: int,
    time_pct: float,
    loc_pct: float,
) -> str:
    parts = (
        f"{f_ghz:.4f}",
        f"{round(phi_t, _COORD_ROUND)}",
        f"{round(lam_t, _COORD_ROUND)}",
        f"{round(phi_r, _COORD_ROUND)}",
        f"{round(lam_r, _COORD_ROUND)}",
        f"{round(htg, 1)}",
        f"{round(hrg, 1)}",
        str(pol),
        str(zone),
        f"{time_pct:.0f}",
        f"{loc_pct:.0f}",
        _profile_digest(d_km, h_m),
    )
    return _REDIS_KEY_PREFIX + ":".join(parts)


_LRU_MAX = 2048
_LRU: "OrderedDict[str, float]" = OrderedDict()
_LRU_LOCK = threading.Lock()


def _lru_get(key: str) -> Optional[float]:
    with _LRU_LOCK:
        v = _LRU.get(key)
        if v is not None:
            _LRU.move_to_end(key)
        return v


def _lru_put(key: str, val: float) -> None:
    with _LRU_LOCK:
        _LRU[key] = val
        _LRU.move_to_end(key)
        while len(_LRU) > _LRU_MAX:
            _LRU.popitem(last=False)


# ---------------------------------------------------------------------------
# Public predict
# ---------------------------------------------------------------------------

def predict_basic_loss(
    *,
    f_hz: float,
    d_km: Sequence[float],
    h_m: Sequence[float],
    htg: float,
    hrg: float,
    phi_t: float,
    lam_t: float,
    phi_r: float,
    lam_r: float,
    clutter_heights_m: Optional[Sequence[float]] = None,
    pol: Optional[int] = None,
    zone: Optional[int] = None,
    time_pct: Optional[float] = None,
    loc_pct: Optional[float] = None,
) -> Optional[float]:
    """Return ITU-R P.1812 basic transmission loss ``Lb`` in dB.

    Returns ``None`` when ``Py1812`` is unavailable, when inputs fall
    outside the recommendation's domain (f outside 30 MHz–6 GHz,
    profile too short, etc.), or when the underlying call raises —
    the caller is expected to fall back to the ridge model.

    The implementation is read-mostly: identical (rounded) requests
    are served from the in-process LRU(2048) → Redis(TTL 7 d) layer
    before incurring the ~30-80 ms cost of ``P1812.bt_loss``.
    """
    P1812 = _try_import_p1812()
    if P1812 is None:
        return None

    # P.1812 is defined for 30 MHz ≤ f ≤ 6 GHz.
    if not (30e6 <= f_hz <= 6e9):
        return None

    d_arr = np.asarray(d_km, dtype=np.float64)
    h_arr = np.asarray(h_m, dtype=np.float64)
    if d_arr.size < 2 or d_arr.size != h_arr.size:
        return None
    # Profile must be ascending and span ≥ 0.25 km.
    if d_arr[-1] - d_arr[0] < 0.25:
        return None

    # Force ascending from 0 (P.1812 expects distance from Tx).
    if d_arr[0] != 0.0:
        d_arr = d_arr - d_arr[0]

    f_ghz = f_hz / 1e9
    pol_v = pol if pol is not None else _POL
    zone_v = zone if zone is not None else _ZONE_DEFAULT
    time_v = time_pct if time_pct is not None else _TIME_PCT
    loc_v = loc_pct if loc_pct is not None else _LOC_PCT

    # P.1812 wants a per-sample zone array. We use a uniform default;
    # callers with coast-crossing paths should pass per-segment zones
    # via the ``zone`` kwarg in a future revision.
    zone_arr = np.full(d_arr.size, int(zone_v), dtype=np.int32)

    # Representative clutter heights. Default to 0 (no clutter modifier);
    # when the receiver sits in dense urban MapBiomas class, callers may
    # pass a heuristic vertical extent (e.g. 15 m for code 24 = Urban).
    if clutter_heights_m is None:
        R_arr = np.zeros(d_arr.size, dtype=np.float64)
    else:
        R_arr = np.asarray(clutter_heights_m, dtype=np.float64)
        if R_arr.size != d_arr.size:
            R_arr = np.zeros(d_arr.size, dtype=np.float64)

    # Cache lookup
    key = _build_key(
        f_ghz=f_ghz, d_km=d_arr, h_m=h_arr,
        htg=htg, hrg=hrg,
        phi_t=phi_t, lam_t=lam_t, phi_r=phi_r, lam_r=lam_r,
        pol=pol_v, zone=zone_v, time_pct=time_v, loc_pct=loc_v,
    )

    cached = _lru_get(key)
    if cached is not None:
        return float(cached)

    rclient = _get_redis()
    if rclient is not None:
        try:
            raw = rclient.get(key)
            if raw is not None:
                val = float(raw)
                _lru_put(key, val)
                return val
        except Exception as exc:  # noqa: BLE001
            logger.debug("ITU P.1812 redis get failed: %s", exc)

    # Compute. Errors here are non-fatal — return None so caller falls back.
    try:
        Lb, _Ep = P1812.bt_loss(
            f_ghz, time_v, d_arr, h_arr, R_arr, zone_arr,
            float(htg), float(hrg),
            int(pol_v),
            float(phi_t), float(phi_r),
            float(lam_t), float(lam_r),
            pL=float(loc_v),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Py1812.bt_loss failed: %s", exc)
        return None

    Lb_f = float(np.asarray(Lb).reshape(-1)[0])
    if not np.isfinite(Lb_f):
        return None

    # Cache write
    _lru_put(key, Lb_f)
    if rclient is not None:
        try:
            rclient.setex(key, _REDIS_TTL_SEC, json.dumps(Lb_f))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ITU P.1812 redis set failed: %s", exc)
    return Lb_f


# ---------------------------------------------------------------------------
# Test hooks
# ---------------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Clear caches and force re-import on next call (test fixture)."""
    global _p1812_module, _p1812_import_failed, _redis_client, _redis_init_failed
    _p1812_module = None
    _p1812_import_failed = False
    _redis_client = None
    _redis_init_failed = False
    with _LRU_LOCK:
        _LRU.clear()
