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


def _make_app(*, tier="business", owner="tenant-a", api_key="ttp_test_key",
              is_admin=False):
    """Build a TestClient app whose middleware seeds request.state with
    the auth context that production's `verify_api_key` would set."""
    app = FastAPI()

    @app.middleware("http")
    async def _seed_auth(request, call_next):
        request.state.tier = tier
        request.state.owner = owner
        request.state.api_key = api_key
        request.state.is_admin = is_admin
        return await call_next(request)

    app.include_router(rrouter.router)
    return app


@pytest.fixture
def client(fakes):
    return TestClient(_make_app())


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


# ── Tier gating ──────────────────────────────────────────────────

@pytest.mark.parametrize("tier", ["free", "starter", "pro", "", "unknown"])
def test_submit_403_for_low_or_unknown_tier(fakes, tier):
    """Sionna RT is restricted to BUSINESS / ENTERPRISE / ULTRA."""
    app = _make_app(tier=tier)
    client = TestClient(app)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 403
    detail = r.json()["detail"].lower()
    assert "business" in detail or "tier" in detail


@pytest.mark.parametrize("tier", ["business", "enterprise", "ultra"])
def test_submit_accepts_paying_tiers(fakes, tier):
    app = _make_app(tier=tier)
    client = TestClient(app)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202, r.text


def test_admin_bypasses_tier_gate(fakes):
    """Admin keys may submit even when their tenant-tier would deny."""
    app = _make_app(tier="free", is_admin=True)
    client = TestClient(app)
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202, r.text


# ── Per-tier cell caps ───────────────────────────────────────────

@pytest.mark.parametrize("tier,rows,cols,expect_status", [
    # business cap = 40k
    ("business", 200, 200, 202),
    ("business", 201, 200, 403),
    # enterprise cap = 160k
    ("enterprise", 400, 400, 202),
    ("enterprise", 401, 400, 403),
    # ultra cap = 640k (800x800)
    ("ultra", 800, 800, 202),
    ("ultra", 801, 800, 403),
])
def test_per_tier_cell_caps(fakes, tier, rows, cols, expect_status):
    app = _make_app(tier=tier)
    client = TestClient(app)
    body = _payload(raster_grid={
        "rows": rows, "cols": cols,
        "bbox": [-23.56, -46.64, -23.54, -46.62],
    })
    r = client.post("/coverage/engines/sionna-rt/raster", json=body)
    assert r.status_code == expect_status, r.text
    if expect_status == 403:
        assert tier in r.json()["detail"].lower()


# ── OWASP A01: cross-tenant IDOR on GET ──────────────────────────

def test_get_404_when_other_tenant_polls(fakes):
    """Tenant B polling Tenant A's job_id must see 404 (not 200, not 403)."""
    app_a = _make_app(tier="business", owner="tenant-a")
    client_a = TestClient(app_a)
    r = client_a.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    app_b = _make_app(tier="business", owner="tenant-b")
    client_b = TestClient(app_b)
    r2 = client_b.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r2.status_code == 404
    assert r2.json()["detail"] == "job not found or expired"

    # Owner can still poll their own job successfully.
    r3 = client_a.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r3.status_code == 200
    assert r3.json()["status"] in {"queued", "done"}


def test_get_admin_can_poll_any_tenant_job(fakes):
    """Admin keys are allowed cross-tenant reads (impersonation use-case)."""
    app_a = _make_app(tier="business", owner="tenant-a")
    client_a = TestClient(app_a)
    r = client_a.post("/coverage/engines/sionna-rt/raster", json=_payload())
    job_id = r.json()["job_id"]

    app_admin = _make_app(tier="business", owner="admin-ops", is_admin=True)
    client_admin = TestClient(app_admin)
    r2 = client_admin.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    assert r2.status_code == 200


# ── Audit logging ────────────────────────────────────────────────

def test_submit_writes_audit_row(monkeypatch, client, fakes):
    captured: list[dict] = []

    async def _fake_log(api_key, action, **kwargs):
        captured.append({"api_key": api_key, "action": action, **kwargs})

    import audit_log
    monkeypatch.setattr(audit_log, "log", _fake_log)

    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202

    actions = [c["action"] for c in captured]
    assert "coverage.rt.raster.submit" in actions
    submit_row = next(c for c in captured if c["action"] == "coverage.rt.raster.submit")
    assert submit_row["api_key"] == "ttp_test_key"
    assert submit_row["tier"] == "business"
    assert submit_row["target"].startswith("job:")
    assert submit_row["metadata"]["rows"] == 64
    assert submit_row["metadata"]["cols"] == 64


def test_poll_audit_only_on_terminal_transition(monkeypatch, client, fakes):
    """Polls while still queued must not emit poll-audit rows; the row
    appears exactly once on the transition to 'done'."""
    captured: list[dict] = []

    async def _fake_log(api_key, action, **kwargs):
        captured.append({"api_key": api_key, "action": action, **kwargs})

    import audit_log
    monkeypatch.setattr(audit_log, "log", _fake_log)

    sqs, s3 = fakes
    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    job_id = r.json()["job_id"]

    # First poll while object missing — no poll-audit.
    client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    poll_rows = [c for c in captured if c["action"] == "coverage.rt.raster.poll"]
    assert poll_rows == []

    # Worker uploads result; next poll transitions to done and audits.
    s3.store[("ttp-rt-results", f"rasters/{job_id}.npz")] = b"\x00" * 100
    client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    poll_rows = [c for c in captured if c["action"] == "coverage.rt.raster.poll"]
    assert len(poll_rows) == 1
    assert poll_rows[0]["metadata"]["status"] == "done"
    assert poll_rows[0]["metadata"]["raster_bytes"] == 100

    # Subsequent polls (already done) do NOT re-audit.
    client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    client.get(f"/coverage/engines/sionna-rt/raster/{job_id}")
    poll_rows = [c for c in captured if c["action"] == "coverage.rt.raster.poll"]
    assert len(poll_rows) == 1


def test_audit_failure_does_not_break_submit(monkeypatch, client, fakes):
    """A broken audit_log must not propagate as a 500 to the caller."""
    async def _broken(api_key, action, **kwargs):
        raise RuntimeError("audit DB down")

    import audit_log
    monkeypatch.setattr(audit_log, "log", _broken)

    r = client.post("/coverage/engines/sionna-rt/raster", json=_payload())
    assert r.status_code == 202
