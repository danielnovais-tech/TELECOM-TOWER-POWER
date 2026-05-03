# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Shared feature builder for the learned (``sionna``) propagation engine.

Both the offline trainer (``scripts/train_sionna.py``) and the runtime
adapter (``rf_engines.sionna_engine``) MUST go through this module so
the feature schema cannot drift between training and inference. Any
change to the feature set bumps :data:`FEATURE_SCHEMA_VERSION` and
invalidates previously trained TFLite artefacts on load.

Feature vector layout (v1, 28 dims)
-----------------------------------
Index   Name                         Source / formula
-----   ---------------------------- --------------------------------
 0      log10_f_hz                    frequency
 1      d_total_km                    last entry of ``d_km``
 2      htg_m                         tx antenna AGL
 3      hrg_m                         rx antenna AGL
 4      mean_terrain_m                np.mean(h_m)
 5      std_terrain_m                 np.std(h_m)
 6      slope                         linear fit (h_m vs d_km), dB-free
 7      roughness                     RMS first-difference of h_m
 8      max_obstruction_m             max above the tx-top → rx-top LoS line
 9      mean_clearance_m              mean (los_line - h_m), positive = clear
10      n_local_maxima                count of strict local maxima
11      abs_dlat_deg                  |phi_t - phi_r|
12      abs_dlon_deg                  |lam_t - lam_r|
13      bearing_sin                   geometric path bearing
14      bearing_cos
15      pol_h                         1 if pol == 1, else 0
16      zone_inland                   1 if zone == 4 (ITU-R inland), else 0
17      zone_coastal                  1 if zone in (1, 2, 3), else 0
18-27   clutter onehot mean           10-dim mean of MapBiomas one-hot
                                       sampled along the path (zeros if
                                       extractor unavailable; index 27
                                       holds clutter_missing_flag)
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

FEATURE_SCHEMA_VERSION = "v1"
FEATURE_DIM = 28
N_CLUTTER_SAMPLES = 8

FEATURE_NAMES: List[str] = [
    "log10_f_hz",
    "d_total_km",
    "htg_m",
    "hrg_m",
    "mean_terrain_m",
    "std_terrain_m",
    "slope",
    "roughness",
    "max_obstruction_m",
    "mean_clearance_m",
    "n_local_maxima",
    "abs_dlat_deg",
    "abs_dlon_deg",
    "bearing_sin",
    "bearing_cos",
    "pol_h",
    "zone_inland",
    "zone_coastal",
    # 10-dim clutter mean — last slot is the missing flag (mutually exclusive
    # with a real clutter sample, so it doubles as the "Other"-distinct
    # "feature unavailable" indicator).
    "clutter_forest",
    "clutter_savanna",
    "clutter_grassland",
    "clutter_pasture",
    "clutter_mosaic",
    "clutter_urban",
    "clutter_water",
    "clutter_agriculture",
    "clutter_other",
    "clutter_missing_flag",
]
assert len(FEATURE_NAMES) == FEATURE_DIM


def _bearing_rad(phi_t: float, lam_t: float, phi_r: float, lam_r: float) -> float:
    """Initial great-circle bearing tx → rx, radians, range [-π, π]."""
    p1 = math.radians(phi_t)
    p2 = math.radians(phi_r)
    dl = math.radians(lam_r - lam_t)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.atan2(y, x)


def _profile_stats(
    d_km: np.ndarray, h_m: np.ndarray, htg: float, hrg: float
) -> Tuple[float, float, float, float, float, float, int]:
    """Return (mean, std, slope, roughness, max_obs, mean_clear, n_max).

    The LoS line goes from ``(d=0, h_m[0]+htg)`` to ``(d=d_total, h_m[-1]+hrg)``,
    so obstruction is measured against the antenna *tops*, not the bare
    ground — that is what controls diffraction in P.1812 / ITM.
    """
    d_total = float(d_km[-1]) if len(d_km) else 0.0
    if d_total <= 0 or len(h_m) < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0

    mean_t = float(np.mean(h_m))
    std_t = float(np.std(h_m))

    # Linear fit slope of bare terrain (m / km).
    A = np.vstack([d_km, np.ones_like(d_km)]).T
    slope_m_per_km, _ = np.linalg.lstsq(A, h_m, rcond=None)[0]

    # RMS roughness — sensitive to ridges that flatten regression misses.
    roughness = float(np.sqrt(np.mean(np.diff(h_m) ** 2)))

    # LoS line interpolated at every profile sample.
    h_tx_top = h_m[0] + htg
    h_rx_top = h_m[-1] + hrg
    los = h_tx_top + (h_rx_top - h_tx_top) * (d_km / d_total)

    obstruction = h_m - los  # >0 means terrain pokes above the LoS line.
    max_obs = float(np.max(obstruction))
    mean_clear = float(np.mean(los - h_m))  # Inverse sign: positive = clear path.

    # Local-maxima count — proxy for hop count, ITU treats single-knife-edge
    # vs multi-knife-edge as fundamentally different propagation regimes.
    n_max = int(np.sum((h_m[1:-1] > h_m[:-2]) & (h_m[1:-1] > h_m[2:])))

    return mean_t, std_t, float(slope_m_per_km), roughness, max_obs, mean_clear, n_max


def _clutter_mean(
    phi_t: float, lam_t: float, phi_r: float, lam_r: float
) -> Tuple[np.ndarray, bool]:
    """Sample MapBiomas one-hot along the path and return its mean.

    Returns ``(vec10, missing_flag)``. The flag is True when the
    extractor isn't configured (no raster) — callers must propagate
    that bit to the feature vector so the model can learn an
    "unknown morphology" residual instead of treating the zero vector
    as a real prediction.
    """
    try:
        from mapbiomas_clutter import (  # type: ignore[import-not-found]
            clutter_class_to_onehot,
            get_extractor,
        )
    except Exception:
        return np.zeros(10, dtype=np.float64), True

    try:
        ext = get_extractor()
    except Exception:
        return np.zeros(10, dtype=np.float64), True

    # If the extractor exists but has no raster, treat as missing rather
    # than fabricate a class. We probe with a single lookup at the
    # midpoint — that's cheap and tells us whether subsequent samples
    # have a hope of returning anything.
    mid_lat = 0.5 * (phi_t + phi_r)
    mid_lon = 0.5 * (lam_t + lam_r)
    try:
        probe = ext.get_clutter_class(mid_lat, mid_lon)
    except Exception:
        probe = None
    if probe is None:
        return np.zeros(10, dtype=np.float64), True

    vec = np.zeros(10, dtype=np.float64)
    n = 0
    for i in range(N_CLUTTER_SAMPLES):
        f = i / max(1, N_CLUTTER_SAMPLES - 1)
        lat = phi_t + (phi_r - phi_t) * f
        lon = lam_t + (lam_r - lam_t) * f
        try:
            code = ext.get_clutter_class(lat, lon)
        except Exception:
            code = None
        vec += clutter_class_to_onehot(code)
        n += 1
    if n == 0:
        return np.zeros(10, dtype=np.float64), True
    return vec / n, False


def build_features(
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
    pol: Optional[int] = None,
    zone: Optional[int] = None,
) -> np.ndarray:
    """Compute the FEATURE_DIM-dim feature vector for one Tx→Rx link.

    Always returns a finite vector — missing optional inputs (clutter
    extractor, ITU zone, polarisation) collapse to encoded "unknown"
    slots so a single trained model can serve heterogeneous inputs.
    """
    d_arr = np.asarray(d_km, dtype=np.float64)
    h_arr = np.asarray(h_m, dtype=np.float64)
    if d_arr.shape != h_arr.shape or d_arr.ndim != 1:
        raise ValueError("d_km and h_m must be matching 1-D sequences")

    mean_t, std_t, slope, rough, max_obs, mean_clear, n_max = _profile_stats(
        d_arr, h_arr, htg, hrg
    )

    bearing = _bearing_rad(phi_t, lam_t, phi_r, lam_r)
    clutter_vec, missing = _clutter_mean(phi_t, lam_t, phi_r, lam_r)

    out = np.zeros(FEATURE_DIM, dtype=np.float64)
    out[0] = math.log10(max(1.0, float(f_hz)))
    out[1] = float(d_arr[-1]) if len(d_arr) else 0.0
    out[2] = float(htg)
    out[3] = float(hrg)
    out[4] = mean_t
    out[5] = std_t
    out[6] = slope
    out[7] = rough
    out[8] = max_obs
    out[9] = mean_clear
    out[10] = float(n_max)
    out[11] = abs(phi_t - phi_r)
    out[12] = abs(lam_t - lam_r)
    out[13] = math.sin(bearing)
    out[14] = math.cos(bearing)
    out[15] = 1.0 if pol == 1 else 0.0
    out[16] = 1.0 if zone == 4 else 0.0
    out[17] = 1.0 if zone in (1, 2, 3) else 0.0
    # Slots 18..26 = first 9 entries of the clutter mean (Forest..Other).
    # Slot 27 reuses the 10th MapBiomas slot's *position* for the missing
    # flag — the canonical "Other" magnitude is folded into slot 26 only
    # when present. When missing=True the entire clutter block is zeros
    # and slot 27 is set to 1.0; both states are mutually distinguishable.
    if missing:
        out[27] = 1.0
    else:
        out[18:27] = clutter_vec[:9]
        # 10th MapBiomas one-hot bin is the "Other" (rare) bucket; we
        # discard it here to make room for the missing flag and rely on
        # the model to absorb the small information loss.

    if not np.all(np.isfinite(out)):  # defence in depth
        out = np.where(np.isfinite(out), out, 0.0)
    return out


__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "FEATURE_DIM",
    "FEATURE_NAMES",
    "build_features",
]
