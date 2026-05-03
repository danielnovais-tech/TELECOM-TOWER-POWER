# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for ``rf_engines._sionna_features.build_features``.

These tests pin the schema contract that the trainer (offline) and the
SionnaEngine adapter (runtime) both depend on. Any change here is a
breaking change for previously trained TFLite artefacts and must bump
``FEATURE_SCHEMA_VERSION``.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from rf_engines._sionna_features import (
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    build_features,
)


def _flat_link(d_total_km: float = 5.0, n: int = 32) -> dict:
    """A sensible default link the trainer can produce — flat terrain,
    1 GHz, sub-tropical Brazil-ish coordinates. Individual tests
    override the fields they care about."""
    d_km = [d_total_km * i / (n - 1) for i in range(n)]
    h_m = [100.0] * n
    return dict(
        f_hz=900e6,
        d_km=d_km,
        h_m=h_m,
        htg=30.0,
        hrg=2.0,
        phi_t=-15.7,
        lam_t=-47.9,
        phi_r=-15.74,
        lam_r=-47.92,
        pol=1,
        zone=4,
    )


def test_schema_constants_consistent():
    assert FEATURE_DIM == len(FEATURE_NAMES)
    assert FEATURE_SCHEMA_VERSION == "v1"


def test_returns_finite_vector_of_correct_shape():
    out = build_features(**_flat_link())
    assert out.shape == (FEATURE_DIM,)
    assert out.dtype == np.float64
    assert np.all(np.isfinite(out))


def test_log10_frequency_encoding():
    out_900 = build_features(**{**_flat_link(), "f_hz": 900e6})
    out_2400 = build_features(**{**_flat_link(), "f_hz": 2400e6})
    # Index 0 is log10(f_hz) — strict monotone increase.
    assert out_2400[0] > out_900[0]
    assert math.isclose(out_900[0], math.log10(900e6), rel_tol=1e-9)


def test_distance_at_index_one():
    out = build_features(**{**_flat_link(d_total_km=12.5)})
    assert math.isclose(out[1], 12.5, rel_tol=1e-9)


def test_flat_terrain_yields_zero_roughness_and_obstruction():
    out = build_features(**_flat_link())
    # std_terrain (idx 5), slope (6), roughness (7), max_obstruction (8)
    # On a perfectly flat profile with rx_top above ground these all
    # collapse: terrain has no variance and the LoS clears the ground.
    assert out[5] == 0.0
    assert math.isclose(out[6], 0.0, abs_tol=1e-9)
    assert out[7] == 0.0
    # Max obstruction must be ≤ 0 (terrain is at 100m, LoS line spans
    # 130m → 102m).
    assert out[8] <= 0.0


def test_obstruction_detected_on_central_ridge():
    link = _flat_link()
    h = list(link["h_m"])
    h[len(h) // 2] = 500.0   # huge ridge halfway
    link["h_m"] = h
    out = build_features(**link)
    assert out[8] > 200.0   # max_obstruction should be very positive
    assert out[10] >= 1     # at least one local maximum


def test_clutter_missing_flag_when_extractor_absent(monkeypatch):
    """When MapBiomas isn't configured, slot 27 must be 1.0 and the
    9 clutter slots (18..26) must be zero — the model needs to tell
    "feature unavailable" from "feature is the Other class"."""
    # Force the lazy import inside _clutter_mean to fail.
    import rf_engines._sionna_features as feats

    def _raising(*_a, **_kw):
        raise ImportError("simulated missing mapbiomas")

    monkeypatch.setattr(feats, "_clutter_mean",
                        lambda *a, **k: (np.zeros(10), True))
    out = build_features(**_flat_link())
    assert out[27] == 1.0
    assert np.all(out[18:27] == 0.0)


def test_polarization_and_zone_one_hot():
    base = _flat_link()
    out_v = build_features(**{**base, "pol": 2, "zone": 4})
    out_h = build_features(**{**base, "pol": 1, "zone": 1})
    # pol_h = idx 15
    assert out_v[15] == 0.0
    assert out_h[15] == 1.0
    # zone idx 16 (inland=4) and 17 (coastal in {1,2,3})
    assert out_v[16] == 1.0 and out_v[17] == 0.0
    assert out_h[16] == 0.0 and out_h[17] == 1.0


def test_mismatched_profile_lengths_raise():
    link = _flat_link()
    link["h_m"] = link["h_m"][:5]
    with pytest.raises(ValueError):
        build_features(**link)
