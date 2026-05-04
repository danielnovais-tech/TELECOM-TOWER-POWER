# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the ITU-R P.2040-3 material library + evaluator."""
from __future__ import annotations

import json
import logging
import math
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.sources import p2040_materials


# ---------------- library shape ----------------

def test_library_loads_and_validates():
    lib = p2040_materials.load_library()
    assert lib["schema_version"] == 1
    assert "P.2040-3" in lib["table_version"]
    assert set(lib["materials"]) >= {"concrete", "glass", "metal", "vegetation"}


def test_library_sha256_is_stable():
    sha = p2040_materials.library_sha256()
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_validate_rejects_missing_keys(tmp_path):
    bad = {
        "schema_version": 1,
        "materials": {"x": {"a": 1.0, "b": 0, "c": 0, "d": 0}},
        # missing valid_range_ghz
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="valid_range_ghz"):
        p2040_materials.load_library(str(p))


def test_validate_rejects_schema_drift(tmp_path):
    bad = {"schema_version": 99, "materials": {}}
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="schema_version"):
        p2040_materials.load_library(str(p))


# ---------------- evaluator numerics ----------------

def _assert_close(actual, expected, rel=1e-3):
    assert math.isclose(actual, expected, rel_tol=rel), \
        f"{actual} != {expected} (rel_tol={rel})"


def test_concrete_28ghz_matches_p2040_formula():
    """P.2040-3: concrete a=5.31 b=0 c=0.0326 d=0.8095."""
    lib = p2040_materials.load_library()
    r = p2040_materials.evaluate(lib, "concrete", 28e9)
    # epsilon_r' = 5.31 (b=0 \u2192 frequency-independent)
    _assert_close(r["epsilon_r"], 5.31, rel=1e-9)
    # sigma = 0.0326 * 28^0.8095
    expected_sigma = 0.0326 * (28.0 ** 0.8095)
    _assert_close(r["sigma_s_per_m"], expected_sigma, rel=1e-9)
    # epsilon_r'' = sigma / (2 pi f eps0)
    eps0 = 8.8541878128e-12
    expected_imag = expected_sigma / (2 * math.pi * 28e9 * eps0)
    _assert_close(r["epsilon_r_imag"], expected_imag, rel=1e-9)
    assert r["in_valid_range"] is True


def test_glass_60ghz_matches_p2040_formula():
    """P.2040-3: glass a=6.27 b=0 c=0.0043 d=1.1925."""
    lib = p2040_materials.load_library()
    r = p2040_materials.evaluate(lib, "glass", 60e9)
    _assert_close(r["epsilon_r"], 6.27, rel=1e-9)
    expected_sigma = 0.0043 * (60.0 ** 1.1925)
    _assert_close(r["sigma_s_per_m"], expected_sigma, rel=1e-9)


def test_metal_is_near_pec():
    lib = p2040_materials.load_library()
    r = p2040_materials.evaluate(lib, "metal", 39e9)
    assert r["epsilon_r"] == 1.0
    assert r["sigma_s_per_m"] >= 1e6  # essentially PEC
    # Imag part dominates \u2014 loss tangent very large
    assert r["loss_tangent"] > 1e3


def test_vegetation_present_and_lossy():
    """Vegetation is an extension (not in P.2040 Table 3); just smoke-check."""
    lib = p2040_materials.load_library()
    r = p2040_materials.evaluate(lib, "vegetation", 28e9)
    assert 1.0 < r["epsilon_r"] < 5.0
    assert r["sigma_s_per_m"] > 0
    assert lib["materials"]["vegetation"]["p2040_row"] is None


def test_evaluate_unknown_material_raises():
    lib = p2040_materials.load_library()
    with pytest.raises(KeyError, match="not in library"):
        p2040_materials.evaluate(lib, "wood", 28e9)


def test_evaluate_rejects_zero_frequency():
    lib = p2040_materials.load_library()
    with pytest.raises(ValueError, match="frequency_hz"):
        p2040_materials.evaluate(lib, "concrete", 0)


def test_out_of_range_warns_but_evaluates(caplog):
    lib = p2040_materials.load_library()
    # Glass valid_range_ghz lower bound 0.1 GHz; try 0.05 GHz.
    with caplog.at_level(logging.WARNING,
                         logger=p2040_materials.__name__):
        r = p2040_materials.evaluate(lib, "glass", 50e6)
    assert r["in_valid_range"] is False
    assert any("outside tabulated range" in rec.message
               for rec in caplog.records)


# ---------------- evaluate_all ----------------

def test_evaluate_all_default_freqs():
    lib = p2040_materials.load_library()
    out = p2040_materials.evaluate_all(
        lib, lib["frequencies_hz_default"],
    )
    # All four required materials present
    assert set(out.keys()) >= {"concrete", "glass", "metal", "vegetation"}
    for name, entry in out.items():
        assert len(entry["evaluations"]) == 3
        for ev in entry["evaluations"]:
            assert ev["epsilon_r"] > 0
            assert ev["sigma_s_per_m"] >= 0


def test_evaluate_all_subset():
    lib = p2040_materials.load_library()
    out = p2040_materials.evaluate_all(
        lib, [28e9], materials=["concrete", "glass"],
    )
    assert set(out.keys()) == {"concrete", "glass"}
