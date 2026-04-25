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
