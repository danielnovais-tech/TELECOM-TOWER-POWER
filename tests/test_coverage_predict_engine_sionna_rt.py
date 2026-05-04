# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for /coverage/predict?engine=sionna_rt wire-up.

The handler routes ``engine='sionna_rt'`` to the existing async raster
pipeline (``rf_engines_router.sionna_rt_raster_submit``) so all tier
gating, cell caps, SQS dispatch and audit logging stay in one place.
These tests exercise the *delegation* — they're not duplicating the
deeper coverage in ``test_sionna_rt_raster_endpoint.py``.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import Request

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import rf_engines_router as rrouter  # noqa: E402


class _FakeSQS:
    def __init__(self):
        self.sent: list = []

    def send_message(self, *, QueueUrl, MessageBody):
        self.sent.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_QUEUE_URL", "https://sqs.test/queue/sionna-rt")
    monkeypatch.setenv("SIONNA_RT_RESULTS_BUCKET", "ttp-rt-results")
    monkeypatch.setenv("SIONNA_RT_RESULTS_PREFIX", "rasters/")
    rrouter._jobs.clear()


@pytest.fixture
def fake_sqs(monkeypatch, env):
    sqs = _FakeSQS()
    monkeypatch.setattr(rrouter, "_get_sqs", lambda: sqs)
    return sqs


@pytest.fixture
def app_client(monkeypatch, fake_sqs):
    """Spin up the real FastAPI app with verify_api_key mocked."""
    import telecom_tower_power_api as ttpa
    from fastapi.testclient import TestClient

    async def _fake_verify_api_key(request: Request, api_key: str = ""):
        request.state.tier = "business"
        request.state.owner = "tenant-a"
        request.state.api_key = "ttp_test_key"
        request.state.is_admin = False
        return {"tier": ttpa.Tier.BUSINESS, "owner": "tenant-a",
                "is_admin": False}

    ttpa.app.dependency_overrides[ttpa.verify_api_key] = _fake_verify_api_key
    try:
        yield TestClient(ttpa.app), fake_sqs
    finally:
        ttpa.app.dependency_overrides.pop(ttpa.verify_api_key, None)


def _payload(**overrides):
    base = {
        "tower_id": None,
        "tx_lat": -23.5505,
        "tx_lon": -46.6333,
        "tx_height_m": 30.0,
        "tx_power_dbm": 43.0,
        "band": "3500MHz",
        "bbox": [-23.56, -46.64, -23.54, -46.62],
        "grid_size": 32,
        "engine": "sionna_rt",
        "scene_s3_uri": "s3://ttp-scenes/saopaulo-centro",
    }
    base.update(overrides)
    return base


def test_engine_sionna_rt_enqueues_and_returns_job_id(app_client):
    client, sqs = app_client
    r = client.post("/coverage/predict", json=_payload(),
                    headers={"X-API-Key": "x"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert len(body["job_id"]) == 32
    assert body["poll_url"] == \
        f"/coverage/engines/sionna-rt/raster/{body['job_id']}"
    assert body["result_s3_uri"].startswith("s3://ttp-rt-results/rasters/")
    # SQS hit exactly once.
    assert len(sqs.sent) == 1


def test_engine_sionna_rt_without_bbox_returns_422(app_client):
    client, _ = app_client
    body = _payload()
    body.pop("bbox")
    body["rx_lat"] = -23.55
    body["rx_lon"] = -46.63
    r = client.post("/coverage/predict", json=body,
                    headers={"X-API-Key": "x"})
    assert r.status_code == 422
    assert "bbox" in r.text.lower()


def test_engine_sionna_rt_without_scene_uri_returns_422(app_client):
    client, _ = app_client
    body = _payload()
    body.pop("scene_s3_uri")
    r = client.post("/coverage/predict", json=body,
                    headers={"X-API-Key": "x"})
    assert r.status_code == 422
    assert "scene_s3_uri" in r.text.lower()


def test_engine_auto_does_not_hit_sqs(app_client):
    """Sanity check: default engine still runs the sync ML path."""
    client, sqs = app_client
    body = _payload(engine="auto")
    # Don't actually need a valid prediction — we just need to
    # confirm the request is *not* short-circuited into SQS.
    client.post("/coverage/predict", json=body,
                headers={"X-API-Key": "x"})
    assert len(sqs.sent) == 0
