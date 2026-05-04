# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Async interference (T18) — submit / status / result + worker dispatch.

Spins up the FastAPI app with ``verify_api_key`` overridden, monkeypatches
``platform.find_nearest_towers`` to a synthetic fleet, mocks the SQS client,
and drives the SQS Lambda worker against an in-memory job store + a
fake S3 client.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest
from fastapi import Request

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── Shared synthetic fleet (3 co-channel towers around (-15.79, -47.88))

def _fleet(ttpa):
    Tower = ttpa.Tower
    Band = ttpa.Band
    return [
        Tower(id="cc-1", lat=-15.83, lon=-47.92, height_m=30,
              operator="op-a", bands=[Band("2600MHz")], power_dbm=43.0),
        Tower(id="cc-2", lat=-15.85, lon=-47.95, height_m=30,
              operator="op-a", bands=[Band("2600MHz")], power_dbm=43.0),
        Tower(id="cc-3", lat=-15.95, lon=-48.05, height_m=30,
              operator="op-b", bands=[Band("2600MHz")], power_dbm=43.0),
    ]


@pytest.fixture
def app_client(monkeypatch):
    import telecom_tower_power_api as ttpa
    from fastapi.testclient import TestClient

    fleet = _fleet(ttpa)
    monkeypatch.setattr(
        ttpa.platform, "find_nearest_towers",
        lambda lat, lon, operator=None, limit=200, owner=None: list(fleet),
    )

    # Stub the SQS client so submit doesn't hit AWS.
    sent_messages = []

    class _FakeSQS:
        def send_message(self, QueueUrl, MessageBody):
            sent_messages.append({"QueueUrl": QueueUrl, "Body": MessageBody})
            return {"MessageId": "stub"}

    monkeypatch.setattr(ttpa, "_get_sqs", lambda: _FakeSQS())
    # Set a queue URL so the submit path actually attempts to send.
    monkeypatch.setattr(ttpa, "SQS_QUEUE_URL", "https://sqs.test/queue")

    async def _fake_verify_api_key(request: Request, api_key: str = ""):
        request.state.tier = "business"
        request.state.owner = "tenant-a"
        request.state.api_key = "ttp_test_key"
        request.state.is_admin = False
        return {"tier": ttpa.Tier.BUSINESS, "owner": "tenant-a",
                "is_admin": False, "api_key": "ttp_test_key"}

    ttpa.app.dependency_overrides[ttpa.verify_api_key] = _fake_verify_api_key
    try:
        client = TestClient(ttpa.app)
        client._sent_messages = sent_messages  # for assertions
        yield client
    finally:
        ttpa.app.dependency_overrides.pop(ttpa.verify_api_key, None)


def _body(**overrides):
    base = {
        "victim": {"lat": -15.79, "lon": -47.88,
                   "freq_mhz": 2600.0, "bw_mhz": 20.0,
                   "rx_height_m": 10.0, "rx_gain_dbi": 12.0,
                   "noise_figure_db": 5.0},
        "search_radius_km": 100.0, "top_n": 5,
        "include_aci": True, "engine": "auto",
    }
    base.update(overrides)
    return base


# ── Submit endpoint ────────────────────────────────────────────────

def test_async_submit_creates_job_and_enqueues_sqs(app_client):
    r = app_client.post("/coverage/interference/async", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["n_candidates"] == 3
    assert body["poll_url"].endswith("/coverage/interference/jobs/" + body["job_id"])
    assert body["result_url"].endswith("/result")

    # SQS message should have been sent with the right discriminator.
    msgs = app_client._sent_messages
    assert len(msgs) == 1
    msg = json.loads(msgs[0]["Body"])
    assert msg["job_id"] == body["job_id"]
    assert msg["job_type"] == "interference"
    assert msg["tier"] == "business"


def test_async_submit_rejects_sionna_rt(app_client):
    r = app_client.post("/coverage/interference/async",
                        json=_body(engine="sionna-rt"))
    assert r.status_code == 501
    assert "sionna-rt" in r.json()["detail"] or "fspl" in r.json()["detail"]


def test_async_submit_persists_candidates_in_job_payload(app_client):
    import telecom_tower_power_api as ttpa
    r = app_client.post("/coverage/interference/async", json=_body())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    job = ttpa.job_store.get_job(job_id)
    assert job["tower_id"] == ttpa.INTERFERENCE_JOB_SENTINEL
    payload = json.loads(job["receivers"])
    assert payload["job_type"] == "interference"
    assert payload["schema_version"] == 1
    assert len(payload["candidates"]) == 3
    cand = payload["candidates"][0]
    # Schema fields the worker depends on.
    for k in ("aggressor_id", "operator", "lat", "lon",
              "height_m", "f_hz", "bw_hz", "eirp_dbm"):
        assert k in cand


# ── Status / result endpoints ──────────────────────────────────────

def test_status_returns_404_for_pdf_batch_job(app_client):
    import telecom_tower_power_api as ttpa
    # Create a regular PDF job and try to read it as an interference job.
    job_id = str(uuid.uuid4())
    ttpa.job_store.create_job(
        job_id=job_id, tower_id="real-tower-id",
        receivers_json="[]", total=0, api_key="k",
    )
    r = app_client.get(f"/coverage/interference/jobs/{job_id}")
    assert r.status_code == 404


def test_result_409_when_not_completed(app_client):
    r = app_client.post("/coverage/interference/async", json=_body())
    job_id = r.json()["job_id"]
    r2 = app_client.get(f"/coverage/interference/jobs/{job_id}/result")
    assert r2.status_code == 409


# ── Worker dispatch ────────────────────────────────────────────────

def test_worker_processes_interference_job(monkeypatch):
    """End-to-end: submit job, run the worker against a mocked S3,
    fetch the result via the GET endpoint."""
    import telecom_tower_power_api as ttpa
    from fastapi.testclient import TestClient
    import sqs_lambda_worker as worker

    fleet = _fleet(ttpa)
    monkeypatch.setattr(
        ttpa.platform, "find_nearest_towers",
        lambda lat, lon, operator=None, limit=200, owner=None: list(fleet),
    )
    monkeypatch.setattr(ttpa, "_get_sqs", lambda: type("S", (), {
        "send_message": lambda self, **kw: {"MessageId": "x"},
    })())
    monkeypatch.setattr(ttpa, "SQS_QUEUE_URL", "https://sqs.test/queue")

    async def _fake_verify_api_key(request: Request, api_key: str = ""):
        return {"tier": ttpa.Tier.BUSINESS, "owner": "t",
                "is_admin": False, "api_key": "k"}
    ttpa.app.dependency_overrides[ttpa.verify_api_key] = _fake_verify_api_key
    client = TestClient(ttpa.app)
    try:
        # 1) Submit
        r = client.post("/coverage/interference/async", json=_body())
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        # 2) Drive the worker. Mock S3 + force the worker to use the
        #    same job_store the API just wrote to (SQLite path).
        stored_objects = {}

        class _FakeS3:
            def put_object(self, Bucket, Key, Body, ContentType):
                stored_objects[(Bucket, Key)] = Body
                return {}

            def get_object(self, Bucket, Key):
                return {"Body": type("B", (), {
                    "read": lambda self: stored_objects[(Bucket, Key)],
                })()}

        fake_s3 = _FakeS3()
        monkeypatch.setattr(worker, "_get_s3", lambda: fake_s3)
        # Worker reads job from job_store.get_job (sqlite path, since
        # _USE_PG is False unless DATABASE_URL is set in the test env).
        monkeypatch.setattr(worker, "_USE_PG", False)
        monkeypatch.setattr(worker, "S3_BUCKET", "test-bucket")
        monkeypatch.setattr(worker, "S3_PREFIX", "test/")

        # Patch the API's boto3 client used by the result-fetch endpoint
        # to read from the same fake S3.
        import boto3 as real_boto3
        monkeypatch.setattr(real_boto3, "client", lambda *a, **kw: fake_s3)

        worker.handler(
            event={"Records": [{
                "messageId": "m1",
                "body": json.dumps({"job_id": job_id,
                                    "job_type": "interference",
                                    "tier": "business"}),
            }]},
            context=None,
        )

        # 3) Status should be completed and inline result fetchable.
        r2 = client.get(f"/coverage/interference/jobs/{job_id}")
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "completed"

        r3 = client.get(f"/coverage/interference/jobs/{job_id}/result")
        assert r3.status_code == 200, r3.text
        result = r3.json()
        assert result["engine"] == "fspl"
        assert result["n_in_radius"] == 3
        assert result["co_channel_count"] == 3
        assert result["aggregate_i_dbm"] is not None
        assert len(result["top_n_aggressors"]) == 3
    finally:
        ttpa.app.dependency_overrides.pop(ttpa.verify_api_key, None)


def test_worker_marks_failed_on_invalid_payload(monkeypatch):
    import telecom_tower_power_api as ttpa
    import sqs_lambda_worker as worker

    job_id = str(uuid.uuid4())
    ttpa.job_store.create_job(
        job_id=job_id,
        tower_id=ttpa.INTERFERENCE_JOB_SENTINEL,
        receivers_json="not valid json{",
        total=0,
        api_key="k",
    )
    monkeypatch.setattr(worker, "_USE_PG", False)
    worker._process_interference_job(job_id, tier="business")
    job = ttpa.job_store.get_job(job_id)
    assert job["status"] == "failed"
    assert "invalid job payload" in (job["error"] or "")
