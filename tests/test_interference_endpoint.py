# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Integration tests for ``POST /coverage/interference``.

Spins up the FastAPI app with ``verify_api_key`` overridden and the
platform's ``find_nearest_towers`` monkeypatched to a small synthetic
fleet so the test runs offline (no DB).
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import Request

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@pytest.fixture
def app_client(monkeypatch):
    import telecom_tower_power_api as ttpa
    from fastapi.testclient import TestClient

    # Synthetic fleet centred ~5-30 km from the victim @ (-15.79, -47.88).
    # Three co-channel @ 2600 MHz, one adjacent @ 2620 MHz, one far-out
    # @ 700 MHz. Distances tuned so co-channel beats adjacent in dBm.
    Tower = ttpa.Tower
    Band = ttpa.Band
    fleet = [
        Tower(id="cc-1", lat=-15.83, lon=-47.92, height_m=30,
              operator="op-a", bands=[Band("2600MHz")], power_dbm=43.0),
        Tower(id="cc-2", lat=-15.85, lon=-47.95, height_m=30,
              operator="op-a", bands=[Band("2600MHz")], power_dbm=43.0),
        Tower(id="cc-3", lat=-15.95, lon=-48.05, height_m=30,
              operator="op-b", bands=[Band("2600MHz")], power_dbm=43.0),
        # Adjacent: same op, +20 MHz (delta_f/BW = 1.0 → 30 dB ACI)
        # — tower in DB still uses 2600MHz Band; we cheat by mutating
        # primary_freq_hz on a subclass since the Band enum is closed.
    ]

    monkeypatch.setattr(
        ttpa.platform, "find_nearest_towers",
        lambda lat, lon, operator=None, limit=200, owner=None: list(fleet),
    )

    async def _fake_verify_api_key(request: Request, api_key: str = ""):
        request.state.tier = "business"
        request.state.owner = "tenant-a"
        request.state.api_key = "ttp_test_key"
        request.state.is_admin = False
        return {"tier": ttpa.Tier.BUSINESS, "owner": "tenant-a",
                "is_admin": False, "api_key": "ttp_test_key"}

    ttpa.app.dependency_overrides[ttpa.verify_api_key] = _fake_verify_api_key
    try:
        yield TestClient(ttpa.app)
    finally:
        ttpa.app.dependency_overrides.pop(ttpa.verify_api_key, None)


def _body(**overrides):
    base = {
        "victim": {
            "lat": -15.79,
            "lon": -47.88,
            "freq_mhz": 2600.0,
            "bw_mhz": 20.0,
            "rx_height_m": 10.0,
            "rx_gain_dbi": 12.0,
            "noise_figure_db": 5.0,
        },
        "search_radius_km": 100.0,
        "top_n": 5,
        "include_aci": True,
        "engine": "auto",
    }
    base.update(overrides)
    return base


def test_interference_co_channel_aggregation(app_client):
    r = app_client.post("/coverage/interference", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "fspl"
    assert body["n_candidates"] == 3
    assert body["n_in_radius"] == 3
    assert body["co_channel_count"] == 3
    assert body["adjacent_channel_count"] == 0
    assert body["aggregate_i_dbm"] is not None
    # Three aggressors @ 5-30 km, EIRP=60 dBm, FSPL @ 2.6 GHz: each Pr
    # somewhere in the -75 … -110 dBm range. Aggregate must be > strongest.
    top = body["top_n_aggressors"]
    assert len(top) == 3
    assert all(a["aci_db"] == 0.0 for a in top)
    assert top[0]["rx_power_dbm"] >= top[-1]["rx_power_dbm"]
    # I/N must be defined (we have finite contributions); SINR must NOT
    # be present because we didn't pass victim_signal_dbm.
    assert body["i_over_n_db"] is not None
    assert body["sinr_db"] is None
    # Noise floor sanity: kTB+NF for 20 MHz @ NF=5 ≈ -96 dBm.
    assert body["noise_dbm"] == pytest.approx(-95.99, abs=0.05)


def test_interference_with_victim_signal_returns_sinr(app_client):
    r = app_client.post("/coverage/interference",
                        json=_body(victim={
                            "lat": -15.79, "lon": -47.88,
                            "freq_mhz": 2600.0, "bw_mhz": 20.0,
                            "victim_signal_dbm": -75.0,
                        }))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sinr_db"] is not None
    # Strongest aggressor sits well below -75 dBm at 5+ km, so SINR > 0.
    # Concrete bound: SINR ≤ S - N = -75 - (-96) = 21 dB.
    assert body["sinr_db"] < 21.0


def test_interference_radius_filters_out_far_towers(app_client):
    # 2 km radius excludes every synthetic tower (closest is ~5 km).
    r = app_client.post("/coverage/interference",
                        json=_body(search_radius_km=2.0))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_in_radius"] == 0
    assert body["aggregate_i_dbm"] is None
    assert body["i_over_n_db"] is None
    assert body["top_n_aggressors"] == []


def test_interference_unknown_engine_returns_400(app_client):
    r = app_client.post("/coverage/interference",
                        json=_body(engine="bogus"))
    assert r.status_code == 400


def test_interference_sionna_rt_unavailable_returns_503(app_client):
    # Sionna RT is recognised; in CI the engine is not wired
    # (no GPU, no scene file) so the handler reports unavailable
    # and the endpoint surfaces that as HTTP 503.
    r = app_client.post("/coverage/interference",
                        json=_body(engine="sionna-rt"))
    assert r.status_code == 503
    assert "sionna-rt" in r.json()["detail"]


def test_interference_sionna_rt_available_uses_engine(app_client, monkeypatch):
    """When SionnaRTEngine reports available, the endpoint dispatches to
    it for path-loss and the response is_engine="sionna-rt"."""
    from rf_engines import interference_engine as rf_intf
    from rf_engines.base import LossEstimate

    class _FakeEngine:
        def is_available(self):
            return True

        def predict_basic_loss(self, *, f_hz, d_km, h_m, htg, hrg,
                               phi_t, lam_t, phi_r, lam_r):
            # Constant 110 dB, ignoring geometry — deterministic for assertion.
            return LossEstimate(basic_loss_db=110.0, engine="sionna-rt")

    monkeypatch.setattr(
        rf_intf, "SionnaRTEngine", lambda: _FakeEngine(),
    )

    r = app_client.post("/coverage/interference",
                        json=_body(engine="sionna-rt"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "sionna-rt"
    # All 3 synthetic aggressors saw PL=110 dB → identical Pr per aggressor.
    top = body["top_n_aggressors"]
    assert len(top) == 3
    assert all(a["path_loss_db"] == 110.0 for a in top)
    # Co-channel @ 2600 with EIRP=60 dBm → Pr = 60 - 110 + 12 = -38 dBm each.
    for a in top:
        assert a["rx_power_dbm"] == pytest.approx(-38.0, abs=0.01)


def test_interference_sionna_rt_skips_failed_links(app_client, monkeypatch):
    """A None return from predict_basic_loss decrements n_contributing."""
    from rf_engines import interference_engine as rf_intf
    from rf_engines.base import LossEstimate

    class _PartialEngine:
        def __init__(self):
            self._n = 0

        def is_available(self):
            return True

        def predict_basic_loss(self, *, f_hz, d_km, h_m, htg, hrg,
                               phi_t, lam_t, phi_r, lam_r):
            self._n += 1
            # Second call → None (simulate RX outside scene bbox).
            if self._n == 2:
                return None
            return LossEstimate(basic_loss_db=120.0, engine="sionna-rt")

    monkeypatch.setattr(
        rf_intf, "SionnaRTEngine", lambda: _PartialEngine(),
    )

    r = app_client.post("/coverage/interference",
                        json=_body(engine="sionna-rt"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "sionna-rt"
    assert body["n_in_radius"] == 3
    # 1 of 3 aggressors dropped by the engine → 2 contributing.
    assert body["n_contributing"] == 2
    assert len(body["top_n_aggressors"]) == 2


def test_interference_unsupported_engine_returns_501(app_client):
    # ITM and P.1812 are recognised but not yet wired for /interference.
    r = app_client.post("/coverage/interference",
                        json=_body(engine="itu-p1812"))
    assert r.status_code == 501
    assert "itu-p1812" in r.json()["detail"]


def test_interference_co_channel_only_drops_adjacent(app_client, monkeypatch):
    # Add a synthetic adjacent-channel tower (700MHz) — far from victim
    # @ 2600 so |Δ|/BW >> 2.5 → 60 dB ACI (or muted with include_aci=False).
    import telecom_tower_power_api as ttpa
    Tower = ttpa.Tower
    Band = ttpa.Band
    fleet = [
        Tower(id="cc-1", lat=-15.83, lon=-47.92, height_m=30,
              operator="op-a", bands=[Band("2600MHz")], power_dbm=43.0),
        Tower(id="adj-1", lat=-15.84, lon=-47.93, height_m=30,
              operator="op-c", bands=[Band("700MHz")], power_dbm=43.0),
    ]
    monkeypatch.setattr(
        ttpa.platform, "find_nearest_towers",
        lambda lat, lon, operator=None, limit=200, owner=None: list(fleet),
    )

    r = app_client.post("/coverage/interference",
                        json=_body(include_aci=False))
    assert r.status_code == 200, r.text
    body = r.json()
    # Only the co-channel tower should contribute; the adjacent-channel
    # one is hard-muted by include_aci=False.
    assert body["n_in_radius"] == 2
    assert body["n_contributing"] == 1
    assert body["co_channel_count"] == 1
    aggressor_ids = {a["aggressor_id"] for a in body["top_n_aggressors"]}
    assert aggressor_ids == {"cc-1"}
