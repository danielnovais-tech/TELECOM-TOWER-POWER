# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the drive-test CSV importer at
``POST /coverage/observations/drivetest``."""

import importlib
import io

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
    yield TestClient(api.app)
    # Don't leak rate-limit state into sibling tests that share demo keys.
    api._rate_buckets.clear()


_HDRS = {"X-API-Key": "demo_ttp_pro_2604"}

_TX = {
    "tx_lat": "-23.5",
    "tx_lon": "-46.6",
    "tx_height_m": "30.0",
    "tx_power_dbm": "43.0",
    "default_band_mhz": "1800",
    "device": "tems",
}


def _upload(client, csv_text: str, extra: dict | None = None):
    files = {"csv_file": ("drive.csv", csv_text, "text/csv")}
    data = {**_TX, **(extra or {})}
    return client.post(
        "/coverage/observations/drivetest",
        files=files, data=data, headers=_HDRS,
    )


def test_imports_canonical_headers(client):
    csv_text = (
        "lat,lon,signal_dbm,band_mhz,timestamp\n"
        "-23.5101,-46.6002,-85.2,1800,2026-04-15T10:00:00Z\n"
        "-23.5102,-46.6003,-87.4,1800,2026-04-15T10:00:05Z\n"
    )
    r = _upload(client, csv_text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 2
    assert body["skipped"] == 0
    assert body["device"] == "tems"
    assert body["columns_detected"]["signal"] == "signal_dbm"
    assert body["columns_detected"]["band"] == "band_mhz"


def test_imports_tems_style_headers(client):
    """TEMS uses RSRP / Latitude / Longitude / Frequency [MHz]."""
    csv_text = (
        "Latitude,Longitude,RSRP,Frequency [MHz]\n"
        "-23.55,-46.63,-92.1,2600\n"
        "-23.56,-46.64,-95.5,2600\n"
        "-23.57,-46.65,-99.0,2600\n"
    )
    r = _upload(client, csv_text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 3
    assert body["columns_detected"]["lat"] == "Latitude"
    assert body["columns_detected"]["signal"] == "RSRP"
    assert body["columns_detected"]["band"] == "Frequency [MHz]"


def test_imports_gnettrack_style_headers(client):
    """G-NetTrack typical headers."""
    csv_text = (
        "Time,longitude,latitude,RxLev\n"
        "2026-04-15 10:00:00,-46.61,-23.51,-78\n"
        "2026-04-15 10:00:01,-46.62,-23.52,-82\n"
    )
    r = _upload(client, csv_text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 2
    assert body["columns_detected"]["timestamp"] == "Time"
    assert body["columns_detected"]["signal"] == "RxLev"


def test_skips_invalid_rows_but_keeps_valid(client):
    csv_text = (
        "lat,lon,signal_dbm\n"
        "-23.5,-46.6,-90.0\n"
        "not-a-number,-46.6,-90.0\n"
        "200.0,-46.6,-90.0\n"          # lat out of range
        "-23.5,-46.6,5000\n"           # signal out of range
        "-23.5,-46.6,-91.0\n"
    )
    r = _upload(client, csv_text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 2
    assert body["skipped"] == 3


def test_rejects_csv_without_required_columns(client):
    csv_text = "foo,bar\n1,2\n"
    r = _upload(client, csv_text)
    assert r.status_code == 400


def test_requires_tx_context_or_tower_id(client):
    csv_text = (
        "lat,lon,signal_dbm\n"
        "-23.5,-46.6,-90.0\n"
    )
    files = {"csv_file": ("drive.csv", csv_text, "text/csv")}
    # No TX context and no tower_id
    r = client.post(
        "/coverage/observations/drivetest",
        files=files, data={"default_band_mhz": "1800"}, headers=_HDRS,
    )
    assert r.status_code == 422
    assert "tx_lat" in r.text


def test_persists_with_drive_test_source(client):
    csv_text = (
        "lat,lon,signal_dbm,band_mhz\n"
        "-23.5,-46.6,-90.0,1800\n"
        "-23.51,-46.61,-92.0,1800\n"
    )
    r = _upload(client, csv_text)
    assert r.status_code == 200, r.text

    # Check the rows landed in link_observations with source='drive_test'
    from observation_store import ObservationStore
    rows = [r for r in ObservationStore().iter_observations() if r["source"] == "drive_test"]
    assert len(rows) >= 2
    assert all(abs(r["freq_hz"] - 1.8e9) < 1.0 for r in rows[-2:])


def test_free_tier_blocked(client):
    csv_text = "lat,lon,signal_dbm\n-23.5,-46.6,-90.0\n"
    files = {"csv_file": ("drive.csv", csv_text, "text/csv")}
    r = client.post(
        "/coverage/observations/drivetest",
        files=files, data=_TX,
        headers={"X-API-Key": "demo_ttp_free_2604"},
    )
    assert r.status_code in (401, 403)
