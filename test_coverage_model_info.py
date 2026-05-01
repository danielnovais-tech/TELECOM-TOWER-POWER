# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Test the /coverage/model/info endpoint exposes loaded model metadata."""

import importlib
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOWER_DB_PATH", str(tmp_path / "ttp.db"))
    monkeypatch.setenv("OBSERVATION_DB_PATH", str(tmp_path / "obs.db"))
    monkeypatch.setenv("COVERAGE_MODEL_PATH", str(tmp_path / "missing.npz"))
    import observation_store as os_mod
    importlib.reload(os_mod)
    import coverage_predict
    importlib.reload(coverage_predict)
    import telecom_tower_power_api as api
    from fastapi.testclient import TestClient
    return TestClient(api.app)


_HDRS = {"X-API-Key": "demo_ttp_pro_2604"}


def test_model_info_no_local_model(client):
    r = client.get("/coverage/model/info", headers=_HDRS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "sagemaker_endpoint" in data
    assert "local_model" in data
    assert data["local_model"] is None  # no .npz at the configured path


def test_model_info_after_training(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOWER_DB_PATH", str(tmp_path / "ttp.db"))
    monkeypatch.setenv("OBSERVATION_DB_PATH", str(tmp_path / "obs.db"))
    model_path = tmp_path / "coverage_model.npz"
    monkeypatch.setenv("COVERAGE_MODEL_PATH", str(model_path))
    import observation_store as os_mod
    importlib.reload(os_mod)
    import coverage_predict
    importlib.reload(coverage_predict)
    # Train a tiny model and persist it to the configured path.
    coverage_predict.train_model(n_synthetic=200, save_to=str(model_path))
    import telecom_tower_power_api as api
    from fastapi.testclient import TestClient
    c = TestClient(api.app)

    r = c.get("/coverage/model/info?refresh=true", headers=_HDRS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["local_model"] is not None
    lm = data["local_model"]
    assert lm["n_train"] == 200
    assert lm["rmse_db"] >= 0.0
    assert lm["feature_count"] > 0
