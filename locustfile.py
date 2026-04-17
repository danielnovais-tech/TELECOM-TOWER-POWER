"""
locustfile.py – Load test for TELECOM TOWER POWER API.

Targets the SQS/Lambda batch pipeline and core endpoints.

Usage:
    # Install Locust
    pip install locust

    # Run against local/staging
    locust -f locustfile.py --host https://api.telecomtowerpower.com.br

    # Headless (CI-friendly): 100 users, 10/s spawn rate, 5 min duration
    locust -f locustfile.py --host https://api.telecomtowerpower.com.br \
        --headless -u 100 -r 10 -t 5m

    # Target a specific environment
    locust -f locustfile.py --host http://localhost:8000

Environment variables:
    LOCUST_API_KEY   – API key to use (default: demo-key-pro-001)
    LOCUST_TOWER_ID  – Tower ID for analyze/batch (default: VIVO_001)
"""

import csv
import io
import json
import os
import random
import time

from locust import HttpUser, between, tag, task

API_KEY = os.getenv("LOCUST_API_KEY", "demo-key-pro-001")
TOWER_ID = os.getenv("LOCUST_TOWER_ID", "VIVO_001")

# Sample receiver coordinates around Brasília
RECEIVERS = [
    (-15.85 + random.uniform(-0.05, 0.05), -47.81 + random.uniform(-0.05, 0.05))
    for _ in range(200)
]


def _headers():
    return {"X-API-Key": API_KEY}


def _random_receiver():
    lat, lon = random.choice(RECEIVERS)
    return {"lat": lat, "lon": lon, "height_m": 10.0, "antenna_gain_dbi": 12.0}


def _build_csv(num_rows: int) -> bytes:
    """Build an in-memory CSV of receiver points."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["lat", "lon", "height", "gain"])
    for _ in range(num_rows):
        lat, lon = random.choice(RECEIVERS)
        writer.writerow([f"{lat:.6f}", f"{lon:.6f}", "10.0", "12.0"])
    return buf.getvalue().encode("utf-8")


class TelecomUser(HttpUser):
    """Simulates a typical API consumer mixing reads, analysis, and batch jobs."""

    wait_time = between(0.5, 2.0)

    # ── Health / smoke ────────────────────────────────────────

    @tag("smoke")
    @task(5)
    def health_check(self):
        self.client.get("/health")

    # ── Tower CRUD ────────────────────────────────────────────

    @tag("towers")
    @task(10)
    def list_towers(self):
        self.client.get("/towers?limit=20", headers=_headers())

    @tag("towers")
    @task(3)
    def nearest_towers(self):
        rx = _random_receiver()
        self.client.get(
            f"/towers/nearest?lat={rx['lat']}&lon={rx['lon']}&limit=5",
            headers=_headers(),
        )

    # ── Link analysis ─────────────────────────────────────────

    @tag("analyze")
    @task(15)
    def analyze_link(self):
        self.client.post(
            f"/analyze?tower_id={TOWER_ID}",
            headers=_headers(),
            json=_random_receiver(),
        )

    # ── Batch reports (sync — ≤100 rows) ─────────────────────

    @tag("batch", "sync")
    @task(2)
    def batch_sync_small(self):
        """Submit a small batch (10 rows) — processed synchronously."""
        csv_data = _build_csv(10)
        self.client.post(
            f"/batch_reports?tower_id={TOWER_ID}",
            headers=_headers(),
            files={"csv_file": ("receivers.csv", csv_data, "text/csv")},
        )

    # ── Batch reports (async — >100 rows, SQS path) ──────────

    @tag("batch", "async")
    @task(1)
    def batch_async_large(self):
        """Submit a large batch (150 rows) — enqueued to SQS.
        Polls the job until completed or timeout."""
        csv_data = _build_csv(150)
        with self.client.post(
            f"/batch_reports?tower_id={TOWER_ID}",
            headers=_headers(),
            files={"csv_file": ("receivers.csv", csv_data, "text/csv")},
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"Batch submit failed: {resp.status_code}")
                return
            data = resp.json()
            job_id = data.get("job_id")
            if not job_id:
                # Sync response (shouldn't happen with 150 rows, but handle it)
                resp.success()
                return

        # Poll for completion (up to 5 min)
        deadline = time.time() + 300
        while time.time() < deadline:
            with self.client.get(
                f"/jobs/{job_id}",
                headers=_headers(),
                name="/jobs/[job_id]",
                catch_response=True,
            ) as poll:
                if poll.status_code != 200:
                    poll.failure(f"Job poll failed: {poll.status_code}")
                    return
                status = poll.json().get("status")
                if status == "completed":
                    poll.success()
                    # Download the result
                    self.client.get(
                        f"/jobs/{job_id}/download",
                        headers=_headers(),
                        name="/jobs/[job_id]/download",
                    )
                    return
                elif status == "failed":
                    poll.failure("Job failed")
                    return
                poll.success()
            time.sleep(5)

    # ── Repeater planning ─────────────────────────────────────

    @tag("repeater")
    @task(2)
    def plan_repeater(self):
        rx = _random_receiver()
        self.client.post(
            "/plan_repeater",
            headers=_headers(),
            json={
                "tower_id": TOWER_ID,
                "target_lat": rx["lat"],
                "target_lon": rx["lon"],
                "target_height_m": 10.0,
                "max_hops": 3,
            },
        )
