"""Tests for the predicted-vs-measured Prometheus instrumentation
exposed at /metrics after a /coverage/observations POST."""

import os
import importlib

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TOWER_DB_PATH", str(tmp_path / "ttp.db"))
    monkeypatch.setenv("OBSERVATION_DB_PATH", str(tmp_path / "obs.db"))
    import observation_store as os_mod
    importlib.reload(os_mod)
    import telecom_tower_power_api as api
    from fastapi.testclient import TestClient
    return TestClient(api.app)


_HDRS = {"X-API-Key": "demo_ttp_pro_2604"}
_BODY = {
    "tx_lat": -23.5, "tx_lon": -46.6,
    "tx_height_m": 30.0, "tx_power_dbm": 43.0,
    "freq_hz": 900_000_000,
    "rx_lat": -23.51, "rx_lon": -46.61,
    "observed_dbm": -85.0,
}


def _scrape(client) -> str:
    r = client.get("/metrics")
    assert r.status_code == 200
    return r.text


def test_single_observation_records_predicted_measured_residual(client):
    r = client.post("/coverage/observations", json=_BODY, headers=_HDRS)
    assert r.status_code == 200, r.text
    body = _scrape(client)
    assert "coverage_observation_predicted_dbm_count" in body
    assert "coverage_observation_measured_dbm_count" in body
    assert "coverage_observation_residual_db_count" in body
    assert "coverage_observations_total" in body
    # At least one count line should be > 0
    assert any(
        line.startswith("coverage_observation_measured_dbm_count")
        and float(line.rsplit(" ", 1)[1]) >= 1.0
        for line in body.splitlines()
    )


def test_batch_observations_increment_counters(client):
    payload = {"observations": [_BODY, {**_BODY, "observed_dbm": -90.0}]}
    r = client.post("/coverage/observations/batch", json=payload, headers=_HDRS)
    assert r.status_code == 200, r.text
    assert r.json()["ingested"] == 2
    body = _scrape(client)
    counts = [
        float(line.rsplit(" ", 1)[1])
        for line in body.splitlines()
        if line.startswith("coverage_observation_measured_dbm_count{")
    ]
    assert counts and sum(counts) >= 2.0


def test_metric_failure_does_not_break_ingestion(client, monkeypatch):
    """If predict_signal raises, the POST must still 200 and persist."""
    import telecom_tower_power_api as api

    def _boom(**kw):  # noqa: ANN003
        raise RuntimeError("synthetic predict failure")

    monkeypatch.setattr(
        "coverage_predict.predict_signal", _boom, raising=True
    )
    r = client.post("/coverage/observations", json=_BODY, headers=_HDRS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"
