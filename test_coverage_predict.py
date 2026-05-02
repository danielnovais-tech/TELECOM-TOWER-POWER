# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for coverage_predict.py – pure-Python, no AWS required."""

import math
import os
import tempfile

import numpy as np
import pytest

import coverage_predict as cp


def test_feature_vector_shape_and_order():
    feats = cp.build_features(
        d_km=5.0, f_hz=1.8e9, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[100, 150, 200, 180, 120],
        tx_ground_elev_m=100, rx_ground_elev_m=120,
    )
    assert feats.shape == (len(cp.feature_names()),)
    # log_d_km should equal ln(5)
    assert math.isclose(feats[cp.feature_names().index("log_d_km")], math.log(5.0))
    # n_obstructions cannot be negative
    assert feats[cp.feature_names().index("n_obstructions")] >= 0


def test_terrain_summary_handles_empty_profile():
    feats = cp.build_features(
        d_km=2.0, f_hz=900e6, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=None,
    )
    s = dict(zip(cp.feature_names(), feats))
    assert s["terrain_mean_m"] == 0.0
    assert s["min_fresnel_ratio"] == 1.0


def test_train_model_and_persist(tmp_path):
    out = tmp_path / "model.npz"
    model = cp.train_model(n_synthetic=400, save_to=str(out), seed=7)
    assert out.exists()
    assert model.rmse_db < 25.0     # ridge should at least beat the noise floor
    assert model.n_train >= 400

    loaded = cp.CoverageModel.load(str(out))
    assert loaded.weights.shape == model.weights.shape
    assert loaded.rmse_db == pytest.approx(model.rmse_db, rel=1e-6)


def test_predict_signal_falls_back_when_no_model(monkeypatch, tmp_path):
    # Point MODEL_PATH at a non-existent file and clear cache
    monkeypatch.setattr(cp, "MODEL_PATH", str(tmp_path / "missing.npz"))
    monkeypatch.setattr(cp, "_model_cache", None, raising=False)
    monkeypatch.setattr(cp, "SAGEMAKER_ENDPOINT", "")

    result = cp.predict_signal(
        d_km=1.0, f_hz=900e6, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[50, 55, 60, 55, 50],
        tx_ground_elev_m=50, rx_ground_elev_m=50,
    )
    assert result.source == "physics-fallback"
    # 1 km at 900 MHz with reasonable gains/power should be a usable link
    assert -100 < result.signal_dbm < 0
    assert isinstance(result.feasible, bool)


def test_predict_uses_local_model(monkeypatch, tmp_path):
    out = tmp_path / "model.npz"
    cp.train_model(n_synthetic=400, save_to=str(out), seed=7)
    monkeypatch.setattr(cp, "MODEL_PATH", str(out))
    monkeypatch.setattr(cp, "_model_cache", None, raising=False)
    monkeypatch.setattr(cp, "SAGEMAKER_ENDPOINT", "")

    result = cp.predict_signal(
        d_km=2.5, f_hz=2.1e9, tx_h_m=40, rx_h_m=15,
        terrain_profile=[100, 110, 130, 115, 105],
        tx_ground_elev_m=100, rx_ground_elev_m=105,
    )
    assert result.source == "local-model"
    assert result.confidence > 0.3
    assert -140 <= result.signal_dbm <= 30


def test_obstructed_path_predicts_lower_signal():
    # Same link, two profiles: clear vs. tall mountain in the middle.
    base_kwargs = dict(
        d_km=4.0, f_hz=1.8e9, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        tx_ground_elev_m=100, rx_ground_elev_m=100,
    )
    clear = cp.predict_signal(
        terrain_profile=[100, 100, 100, 100, 100], **base_kwargs
    )
    blocked = cp.predict_signal(
        terrain_profile=[100, 100, 250, 100, 100], **base_kwargs
    )
    assert blocked.signal_dbm < clear.signal_dbm


# ---------------------------------------------------------------------------
# Band-aware model tests
# ---------------------------------------------------------------------------

def test_nearest_band_mhz_snaps_to_known_bands():
    assert cp._nearest_band_mhz(700e6) == 700
    assert cp._nearest_band_mhz(1.95e9) == 1800   # midway → ties broken by order
    assert cp._nearest_band_mhz(2.0e9) == 2100    # 100 MHz closer to 2100
    assert cp._nearest_band_mhz(3.6e9) == 3500
    assert cp._nearest_band_mhz(900e6) == 900
    # Far out of range still picks the closest nominal band rather than crashing
    assert cp._nearest_band_mhz(28e9) == 3500
    assert cp._nearest_band_mhz(450e6) == 700


def test_train_band_aware_model_persists_per_band(tmp_path):
    out_dir = tmp_path / "bands"
    ba = cp.train_band_aware_model(
        n_synthetic=2000, save_to_dir=str(out_dir),
        seed=11, kfold=0, train_global_fallback=True,
    )
    # All 7 nominal bands should get an artefact when 2 000 samples are
    # spread evenly over them (~285 / band, well above _MIN_SAMPLES_PER_BAND).
    for band in cp._NOMINAL_BANDS_MHZ:
        assert (out_dir / f"coverage_model_{band}.npz").exists(), band
    assert (out_dir / "coverage_model_global.npz").exists()
    assert (out_dir / "manifest.json").exists()
    # Round-trip
    reloaded = cp.BandAwareCoverageModel.load_dir(str(out_dir))
    assert set(reloaded.models.keys()) == set(ba.models.keys())
    assert reloaded.global_model is not None


def test_band_aware_predict_uses_correct_band(tmp_path):
    out_dir = tmp_path / "bands"
    ba = cp.train_band_aware_model(
        n_synthetic=2000, save_to_dir=str(out_dir),
        seed=23, kfold=0, train_global_fallback=False,
    )
    feats = cp.build_features(
        d_km=3.0, f_hz=700e6, tx_h_m=40, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[80, 90, 100, 90, 85],
        tx_ground_elev_m=80, rx_ground_elev_m=85,
    )
    rssi_700, used = ba.predict(feats, f_hz=700e6)
    assert used == 700
    assert -130 < rssi_700 < 30
    # 3.5 GHz on the same path should yield a noticeably lower RSSI
    feats_high = cp.build_features(
        d_km=3.0, f_hz=3.5e9, tx_h_m=40, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[80, 90, 100, 90, 85],
        tx_ground_elev_m=80, rx_ground_elev_m=85,
    )
    rssi_3500, used_high = ba.predict(feats_high, f_hz=3.5e9)
    assert used_high == 3500
    assert rssi_3500 < rssi_700      # higher freq → more FSPL


def test_band_aware_falls_back_when_band_missing(tmp_path):
    out_dir = tmp_path / "bands"
    cp.train_band_aware_model(
        n_synthetic=2000, save_to_dir=str(out_dir),
        seed=5, kfold=0, train_global_fallback=False,
    )
    # Manually remove the 700 MHz artefact to simulate a sparse retrain
    os.remove(out_dir / "coverage_model_700.npz")
    reloaded = cp.BandAwareCoverageModel.load_dir(str(out_dir))
    assert 700 not in reloaded.models
    # Asking for 700 MHz must not crash; it should snap to fallback (1800 MHz)
    picked, band = reloaded.pick(700e6)
    assert picked is not None
    assert band == cp._FALLBACK_BAND_MHZ


def test_predict_signal_uses_band_aware_when_configured(monkeypatch, tmp_path):
    out_dir = tmp_path / "bands"
    cp.train_band_aware_model(
        n_synthetic=2000, save_to_dir=str(out_dir),
        seed=42, kfold=0, train_global_fallback=False,
    )
    monkeypatch.setattr(cp, "BAND_MODEL_DIR", str(out_dir))
    monkeypatch.setattr(cp, "_band_model_cache", None, raising=False)
    monkeypatch.setattr(cp, "_model_cache", None, raising=False)
    monkeypatch.setattr(cp, "SAGEMAKER_ENDPOINT", "")
    # Disable global single-band model so we can detect band-aware was used
    monkeypatch.setattr(cp, "MODEL_PATH", str(tmp_path / "missing.npz"))

    result = cp.predict_signal(
        d_km=2.0, f_hz=2.6e9, tx_h_m=35, rx_h_m=10,
        terrain_profile=[100, 110, 120, 110, 105],
        tx_ground_elev_m=100, rx_ground_elev_m=105,
    )
    assert result.source == "local-model-band"
    assert "band-2600MHz" in result.model_version


# ---------------------------------------------------------------------------
# Clutter feature schema (Step 4: future-proof model artefacts)
# ---------------------------------------------------------------------------

def test_build_features_with_clutter_extends_dimension():
    base = cp.build_features(
        d_km=1.0, f_hz=900e6, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[100, 105, 110, 105, 100],
    )
    extended = cp.build_features(
        d_km=1.0, f_hz=900e6, tx_h_m=30, rx_h_m=10,
        tx_power_dbm=43, tx_gain_dbi=17, rx_gain_dbi=12,
        terrain_profile=[100, 105, 110, 105, 100],
        with_clutter=True,
    )
    assert extended.shape == (base.shape[0] + 10,)
    # The 10 appended dims must form a one-hot (sum = 1).
    assert extended[base.shape[0]:].sum() == pytest.approx(1.0)


def test_feature_names_with_clutter_appends_one_hot_columns():
    base_names = cp.feature_names()
    extended_names = cp.feature_names(with_clutter=True)
    assert len(extended_names) == len(base_names) + 10
    # First 17 columns are the v1 schema, unchanged.
    assert extended_names[: len(base_names)] == base_names
    # Last 10 columns are clutter_*.
    assert all(n.startswith("clutter_") for n in extended_names[len(base_names):])


def test_coveragemodel_persists_feature_names(tmp_path):
    out = tmp_path / "model.npz"
    model = cp.train_model(n_synthetic=400, save_to=str(out), seed=7, kfold=0)
    # Default schema is v1 (17 features); the artefact should round-trip
    # the names so a future loader can detect schema upgrades.
    reloaded = cp.CoverageModel.load(str(out))
    assert reloaded.feature_names == model.feature_names
    assert len(reloaded.feature_names) == len(cp.feature_names())
