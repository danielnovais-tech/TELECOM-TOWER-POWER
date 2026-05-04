# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for Tijolo 6 — POST /coverage/engines/sionna-rt/raster endpoint.

The endpoint is a kick-and-poll wrapper around the GPU worker pool:

* ``POST`` enqueues an SQS message that the T5 worker
  (``scripts.sionna_rt_worker``) parses with :func:`parse_job_message`.
  We round-trip the API-emitted body through that parser to lock in
  the schema contract end-to-end — drift breaks the test, not prod.
* ``GET`` polls S3 (``head_object`` on the ``result_s3_uri``); status
  flips to ``done`` and a presigned URL is returned once the object
  exists.
"""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import rf_engines_router as rrouter  # noqa: E402
from scripts import sionna_rt_worker as worker  # noqa: E402


# ── Fakes ────────────────────────────────────────────────────────

class _FakeSQS:
    def __init__(self):
        self.sent: list[dict] = []

    def send_message(self, *, QueueUrl, MessageBody):
        self.sent.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})


class _FakeS3:
    """In-memory stand-in for the boto3 S3 client.

    ``store`` maps ``(bucket, key) -> bytes``. ``head_object`` raises
    :class:`KeyError` when the object is absent — the endpoint catches
    any exception and treats it as "not yet ready".
    """

    def __init__(self, store: dict | None = None):
        self.store: dict[tuple[str, str], bytes] = dict(store or {})
        self.presigned_calls: list[dict] = []

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.store:
            raise KeyError(f"NoSuchKey: {Bucket}/{Key}")
        return {"ContentLength": len(self.store[(Bucket, Key)])}

    def generate_presigned_url(self, op, *, Params, ExpiresIn):
        self.presigned_calls.append(
            {"op": op, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_QUEUE_URL", "https://sqs.test/queue/sionna-rt")
    monkeypatch.setenv("SIONNA_RT_RESULTS_BUCKET", "ttp-rt-results")
    monkeypatch.setenv("SIONNA_RT_RESULTS_PREFIX", "rasters/")
    monkeypatch.setenv("SIONNA_RT_PRESIGN_TTL_S", "3600")
    # Reset the in-memory job store between tests.
    rrouter._jobs.clear()


@pytest.fixture
def fakes(monkeypatch, env):
    sqs = _FakeSQS()
    s3 = _FakeS3()
    monkeypatch.setattr(rrouter, "_get_sqs", lambda: sqs)
    monkeypatch.setattr(rrouter, "_get_s3", lambda: s3)
    return sqs, s3


@pytest.fixture
def client(fakes):
    app = FastAPI()
    app.include_router(rrouter.router)
    return TestClient(app)


def _payload(**overrides):
    base = {
        "scene_s3_uri": "s3://ttp-scenes/saopaulo-centro",
        "tx": {
            "lat": -23.5505,
            "lon": -46.6333,
            "height_m": 30.0,
            "power_dbm": 43.0,
        },
        "frequency_hz": 28e9,
        "raster_grid": {
            "rows": 64,
            "cols": 64,
            "bbox": [-23.56, -46.64, -23.54, -46.62],
        },
    }
    base.update(overrides)
    return base


# ── Tests ────────────────────────────────────────────────────────

def test_submit_returns_job_id_and_enqueues_sqs(client, fakes):
    sqs, _ = fakes
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]
    assert len(job_id) == 32  # uuid4().hex
    assert body["poll_url"] == f"/coverage/engines/sionna-rt/raster/{job_id}"
    assert body["result_s3_uri"] == f"s3://ttp-rt-results/rasters/{job_id}.npz"

    # Exactly one SQS message dispatched.
    assert len(sqs.sent) == 1
    msg = sqs.sent[0]
    assert msg["QueueUrl"] == "https://sqs.test/queue/sionna-rt"


def test_submit_message_round_trips_through_worker_parser(client, fakes):
    """Round-trip lock: the API body must parse cleanly through the T5 worker.

    Drift in either schema breaks this test before it breaks prod.
    """
    sqs, _ = fakes
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202

    msg_body = sqs.sent[0]["MessageBody"]
    job = worker.parse_job_message(msg_body)
    assert job.job_id == r.json()["job_id"]
    assert job.frequency_hz == 28e9
    assert job.tx_lat == -23.5505
    assert job.tx_lon == -46.6333
    assert job.tx_height_m == 30.0
    assert job.tx_power_dbm == 43.0
    assert job.rows == 64 and job.cols == 64
    assert job.bbox_south == -23.56
    assert job.bbox_north == -23.54
    assert job.bbox_west == -46.64
    assert job.bbox_east == -46.62
    assert job.scene_s3_uri == "s3://ttp-scenes/saopaulo-centro/"
    assert job.result_s3_uri == r.json()["result_s3_uri"]


def test_submit_503_when_queue_url_unset(monkeypatch, fakes, client):
    monkeypatch.delenv("SIONNA_RT_QUEUE_URL", raising=False)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 503
    assert "SIONNA_RT_QUEUE_URL" in r.json()["detail"]


def test_submit_503_when_results_bucket_unset(monkeypatch, fakes, client):
    monkeypatch.delenv("SIONNA_RT_RESULTS_BUCKET", raising=False)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 503
    assert "SIONNA_RT_RESULTS_BUCKET" in r.json()["detail"]


def test_submit_502_when_sqs_send_raises(monkeypatch, client, fakes):
    sqs, _ = fakes

    def _boom(**_kw):
        raise RuntimeError("aws unreachable")

    monkeypatch.setattr(sqs, "send_message", _boom)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 502
    assert "queue send failed" in r.json()["detail"]


@pytest.mark.parametrize("bad_body", [
    # bbox south > north
    _payload(raster_grid={"rows": 8, "cols": 8,
                          "bbox": [10.0, -10.0, 5.0, 0.0]}),
    # bbox west > east
    _payload(raster_grid={"rows": 8, "cols": 8,
                          "bbox": [0.0, 10.0, 5.0, -5.0]}),
    # too many cells
    _payload(raster_grid={"rows": 2000, "cols": 2001,
                          "bbox": [-1.0, -1.0, 1.0, 1.0]}),
    # frequency below 1 MHz
    _payload(frequency_hz=1.0),
    # frequency above 300 GHz
    _payload(frequency_hz=4e11),
    # scene not s3://
    _payload(scene_s3_uri="https://not-s3.example/scene"),
    # tx.lat out of range
    _payload(tx={"lat": 91.0, "lon": 0.0, "height_m": 30.0, "power_dbm": 40.0}),
])
def test_submit_422_on_invalid_body(client, fakes, bad_body):
    r = client.post("/coverage/engines/sionna-rt/raster", json=bad_body)
    assert r.status_code == 422


def test_status_404_for_unknown_job(client, fakes):
    r = client.get("/coverage/engines/sionna-rt/raster/deadbeef")
    assert r.status_code == 404


def test_status_queued_when_object_missing(client, fakes):
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    job_id = r.json()["job_id"]

    r2 = client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "queued"
    assert body["raster_url"] is None
    assert body["raster_bytes"] is None
    assert body["finished_at"] is None
    assert body["result_s3_uri"] == f"s3://ttp-rt-results/rasters/{job_id}.npz"


def test_status_done_returns_presigned_url_when_object_present(client, fakes):
    sqs, s3 = fakes
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    job_id = r.json()["job_id"]

    # Worker has finished — synthetic .npz lands in our fake S3.
    s3.store[("ttp-rt-results", f"rasters/{job_id}.npz")] = b"\x00" * 12345

    r2 = client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "done"
    assert body["raster_bytes"] == 12345
    assert body["finished_at"] is not None
    assert body["raster_url"].startswith("https://ttp-rt-results.s3")
    assert "X-Amz-Expires=3600" in body["raster_url"]

    # Subsequent polls remain done and reuse the same finished_at.
    finished_first = body["finished_at"]
    r3 = client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r3.json()["status"] == "done"
    assert r3.json()["finished_at"] == finished_first


def test_reaper_drops_old_jobs(monkeypatch, client, fakes):
    """The in-memory tracker honours its TTL."""
    monkeypatch.setenv("SIONNA_RT_JOBS_TTL_S", "60")
    rrouter._JOBS_TTL_S = 60

    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    job_id = r.json()["job_id"]
    assert job_id in rrouter._jobs

    # Fast-forward the recorded creation time past the TTL and force a reap.
    rrouter._jobs[job_id]["created_at"] -= 120
    rrouter._reap_jobs()
    assert job_id not in rrouter._jobs

    # Polling a reaped job is a 404 (not a 500).
    r2 = client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r2.status_code == 404
