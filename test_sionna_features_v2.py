# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for opt-in Sionna feature schema v2 (Planet NDVI delta).

These tests reload ``rf_engines._sionna_features`` after flipping the
``SIONNA_FEATURES_VERSION`` env var because the schema constants are
captured at import time. We restore v1 at the end of every test so
unrelated tests in the same pytest run keep observing the deployed
schema.
"""
from __future__ import annotations

import importlib
import json
import os
from typing import Iterator

import numpy as np
import pytest


def _flat_link(d_total_km: float = 5.0, n: int = 32) -> dict:
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


@pytest.fixture
def v2_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Reload ``_sionna_features`` with v2 enabled, then restore v1."""
    monkeypatch.setenv("SIONNA_FEATURES_VERSION", "v2")
    import rf_engines._sionna_features as mod
    importlib.reload(mod)
    try:
        yield mod
    finally:
        monkeypatch.delenv("SIONNA_FEATURES_VERSION", raising=False)
        importlib.reload(mod)  # restore v1 globals for the rest of the run


@pytest.fixture
def ndvi_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> str:
    cache = tmp_path / "ndvi.json"
    cache.write_text(json.dumps({
        "schema": "ndvi-delta-v1",
        "resolution_deg": 0.05,
        "cells": {
            # Brasília-ish bucket covering both endpoints of _flat_link.
            "-15.70,-47.90": 0.10,
            "-15.75,-47.90": 0.20,
        },
    }))
    monkeypatch.setenv("PLANET_NDVI_CACHE", str(cache))
    import planet_ndvi
    planet_ndvi.reset_for_tests()
    yield str(cache)
    planet_ndvi.reset_for_tests()


def test_v2_schema_constants(v2_module):
    assert v2_module.FEATURE_SCHEMA_VERSION == "v2"
    assert v2_module.FEATURE_DIM == 30
    assert len(v2_module.FEATURE_NAMES) == 30
    assert v2_module.FEATURE_NAMES[28] == "ndvi_delta_mean"
    assert v2_module.FEATURE_NAMES[29] == "ndvi_delta_missing_flag"


def test_v2_missing_flag_when_no_cache(v2_module, monkeypatch, tmp_path):
    # Point the extractor at a non-existent path — every lookup misses.
    monkeypatch.setenv("PLANET_NDVI_CACHE", str(tmp_path / "absent.json"))
    import planet_ndvi
    planet_ndvi.reset_for_tests()
    out = v2_module.build_features(**_flat_link())
    assert out.shape == (30,)
    assert out[28] == 0.0
    assert out[29] == 1.0
    planet_ndvi.reset_for_tests()


def test_v2_populated_cache_yields_ndvi_delta(v2_module, ndvi_cache):
    out = v2_module.build_features(**_flat_link())
    assert out.shape == (30,)
    # All path samples fell into populated cells → not missing.
    assert out[29] == 0.0
    # Mean of the two cells covering the path is in [0.10, 0.20].
    assert 0.05 < out[28] < 0.25


def test_v2_partial_cache_sets_missing_flag(v2_module, monkeypatch, tmp_path):
    cache = tmp_path / "partial.json"
    cache.write_text(json.dumps({
        "schema": "ndvi-delta-v1",
        "resolution_deg": 0.05,
        "cells": {"-15.70,-47.90": 0.30},  # only the tx end is populated
    }))
    monkeypatch.setenv("PLANET_NDVI_CACHE", str(cache))
    import planet_ndvi
    planet_ndvi.reset_for_tests()
    # Make rx far enough away to fall into a different cell.
    link = _flat_link()
    link["phi_r"] = -16.5
    link["lam_r"] = -48.7
    out = v2_module.build_features(**link)
    assert out[29] == 1.0  # at least one sample missing
    planet_ndvi.reset_for_tests()


def test_v2_corrupt_cache_treated_as_empty(v2_module, monkeypatch, tmp_path):
    cache = tmp_path / "corrupt.json"
    cache.write_text("{not json")
    monkeypatch.setenv("PLANET_NDVI_CACHE", str(cache))
    import planet_ndvi
    planet_ndvi.reset_for_tests()
    out = v2_module.build_features(**_flat_link())
    assert out[29] == 1.0
    planet_ndvi.reset_for_tests()


def test_planet_ndvi_clamps_out_of_range_values(monkeypatch, tmp_path):
    cache = tmp_path / "bad.json"
    cache.write_text(json.dumps({
        "schema": "ndvi-delta-v1",
        "resolution_deg": 0.05,
        "cells": {
            "-15.70,-47.90": 5.0,    # invalid — outside [-1, +1]
            "-15.75,-47.90": "nan",  # invalid — non-finite
            "-15.80,-47.90": 0.42,   # valid
        },
    }))
    monkeypatch.setenv("PLANET_NDVI_CACHE", str(cache))
    import planet_ndvi
    planet_ndvi.reset_for_tests()
    ext = planet_ndvi.get_extractor()
    assert ext.get_ndvi_delta(-15.70, -47.90) is None
    assert ext.get_ndvi_delta(-15.75, -47.90) is None
    assert ext.get_ndvi_delta(-15.80, -47.90) == 0.42
    planet_ndvi.reset_for_tests()
