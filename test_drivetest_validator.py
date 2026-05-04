# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Drive-test source validator on /coverage/observations.

Rows submitted with `source` starting with `drivetest_` must populate
the rx-side calibration fields (tx_gain_dbi, rx_gain_dbi, cable_loss_db,
rx_height_m) explicitly. The synthetic-friendly defaults (17 dBi /
0 dBi / 0 dB / 1.5 m) are only valid for `source=api` and the offline
synthetic ingest paths; allowing real-world rows to inherit them
would silently bias the trainer's link-budget reconstruction.
"""

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
    monkeypatch.setattr(api, "_DEMO_RATE_LIMIT_RPM", 10_000, raising=False)
    api._rate_buckets.clear()
    from fastapi.testclient import TestClient
    return TestClient(api.app)


_HDRS = {"X-API-Key": "demo_ttp_pro_2604"}

_DRIVETEST_FULL = {
    "tx_lat": -23.5, "tx_lon": -46.6,
    "tx_height_m": 30.0, "tx_power_dbm": 43.0,
    "tx_gain_dbi": 15.0,
    "freq_hz": 900_000_000,
    "rx_lat": -23.51, "rx_lon": -46.61,
    "rx_height_m": 1.7,
    "rx_gain_dbi": 2.0,
    "cable_loss_db": 3.5,
    "observed_dbm": -85.0,
    "source": "drivetest_tems_v1",
}


def test_drivetest_complete_payload_accepted(client):
    r = client.post("/coverage/observations", json=_DRIVETEST_FULL, headers=_HDRS)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"


@pytest.mark.parametrize(
    "missing",
    ["tx_gain_dbi", "rx_gain_dbi", "cable_loss_db", "rx_height_m"],
)
def test_drivetest_missing_calibration_field_rejected(client, missing):
    body = {k: v for k, v in _DRIVETEST_FULL.items() if k != missing}
    r = client.post("/coverage/observations", json=body, headers=_HDRS)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # The validator concatenates all missing field names into one message.
    flat = str(detail)
    assert missing in flat
    assert "drivetest" in flat


def test_drivetest_missing_multiple_fields_rejected(client):
    body = {
        k: v for k, v in _DRIVETEST_FULL.items()
        if k not in ("rx_gain_dbi", "cable_loss_db")
    }
    r = client.post("/coverage/observations", json=body, headers=_HDRS)
    assert r.status_code == 422, r.text
    flat = str(r.json()["detail"])
    assert "rx_gain_dbi" in flat and "cable_loss_db" in flat


def test_api_source_keeps_legacy_defaults(client):
    """Non-drivetest sources must keep behaving as before — defaults OK."""
    body = {
        "tx_lat": -23.5, "tx_lon": -46.6,
        "tx_height_m": 30.0, "tx_power_dbm": 43.0,
        "freq_hz": 900_000_000,
        "rx_lat": -23.51, "rx_lon": -46.61,
        "observed_dbm": -85.0,
        # no source -> defaults to "api"
    }
    r = client.post("/coverage/observations", json=body, headers=_HDRS)
    assert r.status_code == 200, r.text


def test_drivetest_batch_partial_rejected(client):
    """A single malformed row poisons the whole batch (Pydantic 422)."""
    bad = {k: v for k, v in _DRIVETEST_FULL.items() if k != "cable_loss_db"}
    payload = {"observations": [_DRIVETEST_FULL, bad]}
    r = client.post(
        "/coverage/observations/batch", json=payload, headers=_HDRS,
    )
    assert r.status_code == 422, r.text
    assert "cable_loss_db" in str(r.json()["detail"])
