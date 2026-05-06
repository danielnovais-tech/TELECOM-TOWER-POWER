# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
telecom_tower_power_api.py
TELECOM TOWER POWER - Professional telecom engineering platform
with real terrain elevation (Open-Elevation) and REST API (FastAPI).

Run: uvicorn telecom_tower_power_api:app --reload
"""

import collections
import csv
import hashlib
import hmac
import io
import logging
import math
import json
import asyncio
import heapq
import os
import pathlib
import re
import secrets
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiohttp
from dataclasses import dataclass, asdict
from typing import List, Literal, Optional, Dict, Tuple, Any, Iterable
from enum import Enum
from fastapi import FastAPI, HTTPException, Query, Depends, Security, UploadFile, File, Form, Request, WebSocket, WebSocketDisconnect, Header
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
# uvicorn is imported lazily inside ``__main__`` so the module loads cleanly
# under AWS Lambda (Mangum), which does not bundle uvicorn.
from pdf_generator import build_pdf_report
from tier1_pdf_reports import render_coverage_predict_pdf, render_interference_pdf
from srtm_elevation import SRTMReader
import stripe_billing
from tower_db import TowerStore
from job_store import JobStore, JOB_RESULTS_DIR
import audit_log as _audit
import sso_auth as _sso
from auth import saml_service as _saml
from offline_mode import OfflineModeError, is_offline
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pythonjsonlogger import jsonlogger

# ------------------------------------------------------------
# Structured JSON logging
# ------------------------------------------------------------
_json_handler = logging.StreamHandler()
_json_handler.setFormatter(
    jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
)
logging.basicConfig(level=logging.INFO, handlers=[_json_handler])
logger = logging.getLogger("telecom_tower_power")

# ------------------------------------------------------------
# Slack alerting
# ------------------------------------------------------------
_SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# ------------------------------------------------------------
# Scrub secrets from os.environ so they don't linger in /proc/*/environ.
# All modules that need these values have already captured them above.
# ------------------------------------------------------------
for _secret_name in (
    "DATABASE_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    "SES_SMTP_USERNAME", "SES_SMTP_PASSWORD",
    "VALID_API_KEYS", "SLACK_WEBHOOK_URL", "POSTGRES_PASSWORD",
):
    os.environ.pop(_secret_name, None)
del _secret_name

def _alert_slack(message: str) -> None:
    """Fire-and-forget Slack alert. Never raises."""
    if not _SLACK_WEBHOOK_URL:
        return
    import threading
    import urllib.request
    def _send():
        try:
            data = json.dumps({"text": f":rotating_light: *TELECOM TOWER POWER*\n{message}"}).encode()
            req = urllib.request.Request(
                _SLACK_WEBHOOK_URL, data=data,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            logger.debug("Slack alert failed", exc_info=True)
    threading.Thread(target=_send, daemon=True).start()

# ------------------------------------------------------------
# Prometheus metrics
# ------------------------------------------------------------
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds",
    labelnames=["method", "endpoint", "status", "tier"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status", "tier"],
)
RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total",
    "Total rate-limit rejections (429)",
    labelnames=["tier"],
)
BATCH_JOBS_ACTIVE = Gauge(
    "batch_jobs_active",
    "Number of background batch jobs currently running",
)
BATCH_JOB_DURATION = Histogram(
    "batch_jobs_duration_seconds",
    "Time to process a batch job from start to finish",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)
STALE_JOBS_REAPED = Counter(
    "stale_jobs_reaped_total",
    "Total stale running jobs released back to queue by the reaper",
)
HOP_CACHE_OPS = Gauge(
    "hop_cache_ops_total",
    "Hop-viability cache operation counters (hits/misses/errors/puts)",
    labelnames=["op"],
)
# Depth of the batch-job queue, broken down by status.
# Sampled by a background task (see _batch_queue_metrics_updater).
BATCH_QUEUE_DEPTH = Gauge(
    "batch_queue_depth",
    "Number of batch jobs in each state (sampled)",
    labelnames=["status"],
)
# Age of the oldest queued batch job in seconds. 0 when the queue is empty.
# Drives the BatchQueueStuck alert.
BATCH_QUEUE_OLDEST_AGE = Gauge(
    "batch_queue_oldest_age_seconds",
    "Wait time of the oldest queued batch job, in seconds",
)
# In-process rate-limit hit rate (hits/min) over a rolling 1-minute window.
# Derivable from rate(rate_limit_hits_total[1m]) in PromQL, but exposing a
# pre-computed gauge makes dashboards simpler and keeps a value even when a
# scrape is missed.
RATE_LIMIT_HIT_RATE = Gauge(
    "rate_limit_hit_rate_per_minute",
    "Rate-limit rejections per minute (1-minute rolling window, per tier)",
    labelnames=["tier"],
)

# Coverage-model accuracy: predicted vs measured RSSI for ground-truth
# observations submitted via /coverage/observations. The three histograms
# share the same dBm-domain bucket layout so quantile lines from
# `predicted` and `measured` can be plotted on the same Grafana panel.
# Residual = predicted - measured (positive = optimistic prediction).
_DBM_BUCKETS = (-130, -120, -110, -100, -95, -90, -85, -80, -75, -70,
                -60, -50, -40, -30, -20, -10, 0, 10, 30)
_RESIDUAL_BUCKETS = (-40, -30, -20, -15, -10, -6, -3, -1, 0, 1, 3, 6,
                     10, 15, 20, 30, 40)
COVERAGE_PREDICTED_DBM = Histogram(
    "coverage_observation_predicted_dbm",
    "Model-predicted RSSI (dBm) at the time a ground-truth observation was logged",
    labelnames=["source"],
    buckets=_DBM_BUCKETS,
)
COVERAGE_MEASURED_DBM = Histogram(
    "coverage_observation_measured_dbm",
    "Measured RSSI (dBm) submitted via /coverage/observations",
    labelnames=["source"],
    buckets=_DBM_BUCKETS,
)
COVERAGE_RESIDUAL_DB = Histogram(
    "coverage_observation_residual_db",
    "Prediction error (predicted - measured) in dB; >0 = model optimistic",
    labelnames=["source"],
    buckets=_RESIDUAL_BUCKETS,
)
COVERAGE_OBSERVATIONS_TOTAL = Counter(
    "coverage_observations_total",
    "Total ground-truth coverage observations ingested",
    labelnames=["source"],
)
# Model freshness gauges — populated on startup and refreshed by
# /coverage/model/info. The retrain workflow updates the underlying
# coverage_model.npz; tasks pick up the new artifact on next deploy.
COVERAGE_MODEL_RMSE_DB = Gauge(
    "coverage_model_rmse_db",
    "Training RMSE (dB) of the currently loaded coverage model",
)
COVERAGE_MODEL_N_TRAIN = Gauge(
    "coverage_model_n_train",
    "Number of training samples used to fit the loaded coverage model",
)
COVERAGE_MODEL_TRAINED_AT = Gauge(
    "coverage_model_trained_at",
    "Unix epoch seconds when the loaded coverage model was trained",
)
# Cross-validation metrics (added 2026-05) — exposed so Grafana panels
# and tier-1 procurement audits can read out-of-fold accuracy directly,
# not just in-sample training RMSE.
COVERAGE_MODEL_CV_RMSE_DB = Gauge(
    "coverage_model_cv_rmse_db",
    "Mean k-fold holdout RMSE (dB) of the loaded coverage model",
)
COVERAGE_MODEL_CV_RMSE_STD_DB = Gauge(
    "coverage_model_cv_rmse_std_db",
    "Stddev across folds of holdout RMSE (dB)",
)
COVERAGE_MODEL_CV_FOLDS = Gauge(
    "coverage_model_cv_folds",
    "Number of k-fold splits used to evaluate the loaded coverage model",
)
COVERAGE_MODEL_RMSE_BY_MORPHOLOGY_DB = Gauge(
    "coverage_model_rmse_by_morphology_db",
    "Out-of-fold RMSE (dB) bucketed by terrain morphology",
    labelnames=["morphology"],
)
COVERAGE_MODEL_RMSE_BY_BAND_DB = Gauge(
    "coverage_model_rmse_by_band_db",
    "Out-of-fold RMSE (dB) bucketed by commercial band",
    labelnames=["band"],
)

# ------------------------------------------------------------
# Core domain models (same as before, with minor enhancements)
# ------------------------------------------------------------

class Band(str, Enum):
    BAND_700 = "700MHz"
    BAND_850 = "850MHz"
    BAND_900 = "900MHz"
    BAND_1800 = "1800MHz"
    BAND_2100 = "2100MHz"
    BAND_2600 = "2600MHz"
    BAND_3500 = "3500MHz"

    def to_hz(self) -> float:
        return {
            "700MHz": 700e6,
            "850MHz": 850e6,
            "900MHz": 900e6,
            "1800MHz": 1.8e9,
            "2100MHz": 2.1e9,
            "2600MHz": 2.6e9,
            "3500MHz": 3.5e9,
        }[self.value]

@dataclass(frozen=True)
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float          # antenna height above ground
    operator: str
    bands: List[Band]
    owner: str = "system"
    power_dbm: float = 43.0

    def __post_init__(self):
        if not self.id:
            raise ValueError("Tower.id must be a non-empty string")
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"Tower.lat must be in [-90, 90], got {self.lat}")
        if not (-180 <= self.lon <= 180):
            raise ValueError(f"Tower.lon must be in [-180, 180], got {self.lon}")
        if self.height_m < 0:
            raise ValueError(f"Tower.height_m must be >= 0, got {self.height_m}")
        if not self.bands:
            raise ValueError("Tower.bands must contain at least one Band")
        if not (0 <= self.power_dbm <= 80):
            raise ValueError(f"Tower.power_dbm must be in [0, 80], got {self.power_dbm}")

    def primary_freq_hz(self) -> float:
        return self.bands[0].to_hz()

@dataclass(frozen=True)
class Receiver:
    lat: float
    lon: float
    height_m: float = 10.0
    antenna_gain_dbi: float = 12.0

    def __post_init__(self):
        if not (-90 <= self.lat <= 90):
            raise ValueError(f"Receiver.lat must be in [-90, 90], got {self.lat}")
        if not (-180 <= self.lon <= 180):
            raise ValueError(f"Receiver.lon must be in [-180, 180], got {self.lon}")
        if self.height_m < 0:
            raise ValueError(f"Receiver.height_m must be >= 0, got {self.height_m}")

@dataclass(frozen=True)
class LinkResult:
    feasible: bool
    signal_dbm: float
    fresnel_clearance: float
    los_ok: bool
    distance_km: float
    recommendation: str
    terrain_profile: Optional[List[float]] = None
    tx_height_asl: Optional[float] = None
    rx_height_asl: Optional[float] = None

# ------------------------------------------------------------
# Propagation & Link Budget Engine
# ------------------------------------------------------------

class LinkEngine:
    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2) -> float:
        R = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    @staticmethod
    def free_space_path_loss(d_km: float, f_hz: float) -> float:
        d_m = d_km * 1000
        return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km: float, f_hz: float, d1_km: float, d2_km: float) -> float:
        """First Fresnel zone radius (meters) at a point along the path."""
        d1 = d1_km * 1000
        d2 = d2_km * 1000
        c = 299792458
        return math.sqrt((c * d1 * d2) / (f_hz * (d1 + d2)))

    @staticmethod
    def estimate_signal(tx_power_dbm: float, tx_gain_dbi: float, rx_gain_dbi: float,
                        f_hz: float, d_km: float, extra_loss_db: float = 0.0) -> float:
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl - extra_loss_db

    @staticmethod
    def terrain_clearance(terrain_profile: List[float], d_km: float, f_hz: float,
                          tx_h: float, rx_h: float, k_factor: float = 1.33) -> float:
        """
        Returns minimum fraction of first Fresnel zone clearance (0..1+).
        terrain_profile: list of ground heights (m) at equally spaced points.
        k_factor: effective Earth radius factor (4/3 standard atmosphere).
        """
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n-1)
        R_eff = 6371.0 * k_factor  # effective Earth radius in km
        d_total_m = d_km * 1000
        min_clearance = float('inf')
        for i, ground_h in enumerate(terrain_profile):
            d_i = i * step
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            # Earth curvature correction: bulge = d1*d2 / (2*R_eff)
            d1_m = d_i * 1000
            d2_m = (d_km - d_i) * 1000
            earth_bulge = (d1_m * d2_m) / (2 * R_eff * 1000)
            clearance = line_h - ground_h - earth_bulge
            d1 = d_i
            d2 = d_km - d_i
            if d1 <= 0 or d2 <= 0:
                continue
            fresnel_r = LinkEngine.fresnel_radius(d_km, f_hz, d1, d2)
            if fresnel_r > 0:
                min_clearance = min(min_clearance, clearance / fresnel_r)
        return min_clearance if min_clearance != float('inf') else 1.0

# ------------------------------------------------------------
# Elevation service (Open-Elevation with caching)
# ------------------------------------------------------------

class ElevationService:
    """Elevation lookup with three layers: in-memory cache → local SRTM
    tiles → Open-Elevation API (with retry + exponential backoff)."""

    _API_URL = "https://api.open-elevation.com/api/v1/lookup"
    # Worst-case wall time is roughly MAX_RETRIES*TIMEOUT + sum(backoffs).
    # Keep it well under the Cloudflare/edge 60s timeout so a true-cold
    # request to Open-Elevation fails fast and releases the worker instead
    # of cascading 504s while tiles warm up in Redis and on disk.
    _MAX_RETRIES = int(os.getenv("OE_MAX_RETRIES", "2"))
    _BASE_DELAY = float(os.getenv("OE_BASE_DELAY_S", "0.5"))
    _BATCH_TIMEOUT = float(os.getenv("OE_BATCH_TIMEOUT_S", "12"))
    _SINGLE_TIMEOUT = float(os.getenv("OE_SINGLE_TIMEOUT_S", "6"))

    def __init__(self, srtm_dir: str | None = None):
        self.cache: Dict[Tuple[float, float], float] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        # SRTMReader reads SRTM_REDIS_URL first, then falls back to the
        # generic REDIS_URL (same broker used by the batch worker), so a
        # single Redis instance can hold both job state and warm SRTM tiles.
        _redis_url = os.getenv("SRTM_REDIS_URL") or os.getenv("REDIS_URL")
        self.srtm = SRTMReader(
            srtm_dir or os.getenv("SRTM_DATA_DIR", "./srtm_data"),
            redis_url=_redis_url,
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    async def _post_with_retry(self, payload: dict, timeout: float) -> Optional[dict]:
        """POST to Open-Elevation with exponential backoff."""
        session = await self._get_session()
        delay = self._BASE_DELAY
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                async with session.post(
                    self._API_URL, json=payload, timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.warning(
                        "Open-Elevation HTTP %s on attempt %d",
                        resp.status, attempt,
                    )
            except Exception as exc:
                logger.warning(
                    "Open-Elevation attempt %d failed: %s", attempt, exc
                )
            if attempt < self._MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
        return None

    # ------------------------------------------------------------------
    # Single-point elevation
    # ------------------------------------------------------------------

    async def get_elevation(self, lat: float, lon: float) -> float:
        """Return elevation (m).  Cache → SRTM → API → 0."""
        key = (round(lat, 5), round(lon, 5))
        if key in self.cache:
            return self.cache[key]

        # Try local SRTM tile
        srtm_elev = self.srtm.get_elevation(lat, lon)
        if srtm_elev is not None:
            self.cache[key] = srtm_elev
            return srtm_elev

        # Try remote API with retry
        payload = {"locations": [{"latitude": lat, "longitude": lon}]}
        data = await self._post_with_retry(payload, self._SINGLE_TIMEOUT)
        if data:
            elev = data["results"][0]["elevation"]
            self.cache[key] = elev
            return elev

        self.cache[key] = 0.0
        return 0.0

    # ------------------------------------------------------------------
    # Profile (multi-point)
    # ------------------------------------------------------------------

    async def get_profile(
        self, lat1: float, lon1: float, lat2: float, lon2: float,
        num_points: int = 30,
    ) -> List[float]:
        """Return ground heights (m) along the great-circle path.
        Uses cache → SRTM → batch API (with retry) → interpolation."""

        points = []
        for i in range(num_points):
            frac = i / (num_points - 1)
            lat = lat1 + (lat2 - lat1) * frac
            lon = lon1 + (lon2 - lon1) * frac
            points.append((round(lat, 5), round(lon, 5)))

        heights: List[Optional[float]] = [None] * num_points
        need_api: List[int] = []

        for i, (lat, lon) in enumerate(points):
            # 1. Cache
            cached = self.cache.get((lat, lon))
            if cached is not None:
                heights[i] = cached
                continue
            # 2. SRTM
            srtm_elev = self.srtm.get_elevation(lat, lon)
            if srtm_elev is not None:
                self.cache[(lat, lon)] = srtm_elev
                heights[i] = srtm_elev
                continue
            need_api.append(i)

        # 3. Batch API with retry
        if need_api:
            locations = [
                {"latitude": points[i][0], "longitude": points[i][1]}
                for i in need_api
            ]
            data = await self._post_with_retry(
                {"locations": locations}, self._BATCH_TIMEOUT
            )
            if data:
                for j, idx in enumerate(need_api):
                    elev = data["results"][j]["elevation"]
                    self.cache[points[idx]] = elev
                    heights[idx] = elev

        # 4. Fill remaining Nones via interpolation
        for i in range(num_points):
            if heights[i] is not None:
                continue
            left = right = None
            for l in range(i - 1, -1, -1):
                if heights[l] is not None:
                    left = (l, heights[l])
                    break
            for r in range(i + 1, num_points):
                if heights[r] is not None:
                    right = (r, heights[r])
                    break
            if left and right:
                frac = (i - left[0]) / (right[0] - left[0])
                heights[i] = left[1] + (right[1] - left[1]) * frac
            elif left:
                heights[i] = left[1]
            elif right:
                heights[i] = right[1]
            else:
                heights[i] = 0.0
            self.cache[points[i]] = heights[i]

        return heights  # type: ignore[return-value]

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

# ------------------------------------------------------------
# Main Platform (async-aware)
# ------------------------------------------------------------

class TelecomTowerPower:
    def __init__(self):
        self.elevation = ElevationService()
        self.db = TowerStore()
        logger.info("DB backend: %s – %d towers in database",
                     self.db.backend, self.db.count())

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_tower(row: dict) -> Tower:
        return Tower(
            id=row["id"], lat=row["lat"], lon=row["lon"],
            height_m=row["height_m"], operator=row["operator"],
            bands=[Band(b) for b in row["bands"]],
            power_dbm=row["power_dbm"],
            owner=row.get("owner", "system"),
        )

    @staticmethod
    def _tower_to_row(tower: Tower) -> dict:
        return {
            "id": tower.id, "lat": tower.lat, "lon": tower.lon,
            "height_m": tower.height_m, "operator": tower.operator,
            "bands": [b.value for b in tower.bands],
            "power_dbm": tower.power_dbm,
            "owner": getattr(tower, "owner", "system"),
        }

    # ── CRUD (all go through DB) ─────────────────────────────────

    def add_tower(self, tower: Tower):
        self.db.upsert(self._tower_to_row(tower))

    def update_tower(self, tower: Tower):
        self.db.upsert(self._tower_to_row(tower))

    def remove_tower(self, tower_id: str, owner: Optional[str] = None) -> bool:
        return self.db.delete(tower_id, owner=owner)

    def get_tower(self, tower_id: str) -> Optional[Tower]:
        row = self.db.get(tower_id)
        if row is None:
            return None
        return self._row_to_tower(row)

    def list_towers(self, operator: Optional[str] = None,
                    limit: int = 50000, offset: int = 0,
                    owner: Optional[str] = None) -> List[Tower]:
        rows = self.db.list_all(operator=operator, limit=limit, offset=offset, owner=owner)
        return [self._row_to_tower(r) for r in rows]

    def tower_count(self) -> int:
        return self.db.count()

    def find_nearest_towers(self, lat: float, lon: float,
                            operator: Optional[str] = None,
                            limit: int = 5,
                            owner: Optional[str] = None) -> List[Tower]:
        rows = self.db.find_nearest(lat, lon, operator=operator, limit=limit, owner=owner)
        return [self._row_to_tower(r) for r in rows]

    async def analyze_link(self, tower: Tower, receiver: Receiver,
                           terrain_profile: Optional[List[float]] = None) -> LinkResult:
        d_km = LinkEngine.haversine_km(tower.lat, tower.lon, receiver.lat, receiver.lon)
        f_hz = tower.primary_freq_hz()

        tx_gain = 17.0
        rx_gain = receiver.antenna_gain_dbi
        rssi = LinkEngine.estimate_signal(tower.power_dbm, tx_gain, rx_gain, f_hz, d_km)

        los_ok = True
        fresnel_clear = 1.0
        if terrain_profile is None:
            # Fetch real terrain profile
            terrain_profile = await self.elevation.get_profile(
                tower.lat, tower.lon, receiver.lat, receiver.lon
            )
        if terrain_profile:
            # Convert AGL heights to ASL by adding ground elevation at each end
            tx_h_asl = terrain_profile[0] + tower.height_m
            rx_h_asl = terrain_profile[-1] + receiver.height_m
            fresnel_clear = LinkEngine.terrain_clearance(
                terrain_profile, d_km, f_hz, tx_h_asl, rx_h_asl
            )
            los_ok = fresnel_clear > 0.6   # 60% clearance needed for reliable link
            if fresnel_clear < 0.6:
                # Knife-edge diffraction loss saturates ~40 dB (ITU-R P.526);
                # cap so deep negative clearance can't yield unphysical RSSI.
                rssi -= min((0.6 - fresnel_clear) * 10, 40.0)

        feasible = los_ok and (rssi > -95)

        if not los_ok:
            recommendation = f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). Increase receiver height to > {tower.height_m + 10:.0f}m or move tower."
        elif rssi < -95:
            recommendation = f"Signal too low ({rssi:.1f} dBm). Consider higher gain antenna or use a repeater tower at distance {d_km/2:.1f}km."
        else:
            recommendation = f"Good link. RSSI = {rssi:.1f} dBm. Clear LOS."

        return LinkResult(
            feasible=feasible,
            signal_dbm=rssi,
            fresnel_clearance=fresnel_clear,
            los_ok=los_ok,
            distance_km=d_km,
            recommendation=recommendation,
            terrain_profile=terrain_profile if terrain_profile else None,
            tx_height_asl=tx_h_asl if terrain_profile else None,
            rx_height_asl=rx_h_asl if terrain_profile else None,
        )

    async def plan_repeater_chain(self, start_tower: Tower, target_receiver: Receiver,
                                  max_hops: int = 3,
                                  candidate_sites: Optional[List[Tower]] = None) -> List[Tower]:
        """
        Bottleneck-shortest-path multi-hop repeater optimization with
        terrain-aware LOS scoring.

        Finds the path from start_tower to target_receiver (through candidate
        repeater sites) that minimizes the worst single-hop path loss, subject
        to a max_hops constraint.  Hops with obstructed Fresnel zones receive
        additional loss penalties, ensuring the optimizer prefers clear-LOS
        paths even when FSPL alone would allow a longer hop.

        If no candidate_sites are given, generates evenly spaced candidates
        along the great-circle path.
        """
        f_hz = start_tower.primary_freq_hz()
        tx_gain = 17.0
        rx_gain = 12.0  # default repeater receive gain

        # Build candidate list
        if candidate_sites is None:
            candidate_sites = self._generate_candidates(start_tower, target_receiver, max_hops)

        # Virtual target node (receiver treated as a passive site)
        target_node = Tower(
            id="__target__",
            lat=target_receiver.lat, lon=target_receiver.lon,
            height_m=target_receiver.height_m,
            operator=start_tower.operator, bands=start_tower.bands,
            power_dbm=start_tower.power_dbm
        )

        all_nodes: List[Tower] = [start_tower] + candidate_sites + [target_node]
        node_index = {t.id: t for t in all_nodes}

        # Max feasible single-hop distance: solve FSPL = Pt + Gt + Gr - (-95)
        max_hop_km = 10 ** ((start_tower.power_dbm + tx_gain + rx_gain + 95 - 20 * math.log10(f_hz) + 147.55) / 20) / 1000

        # Pre-compute hop costs (FSPL + terrain obstruction penalty).
        # Terrain profiles for every candidate edge are fetched concurrently
        # via asyncio.gather to avoid the O(N^2) sequential await stall that
        # dominated wall time on mountainous paths (SRTM miss -> Open-Elevation
        # HTTP fallback).
        hop_cost: Dict[Tuple[str, str], float] = {}
        pending_edges: List[Tuple[str, str, float, float, int]] = []  # (a_id, b_id, d_km, fspl, num_pts)
        pending_coros: list = []

        for a in all_nodes:
            for b in all_nodes:
                if a.id == b.id or b.id == start_tower.id:
                    continue
                d_km = LinkEngine.haversine_km(a.lat, a.lon, b.lat, b.lon)
                if d_km < 0.1:
                    hop_cost[(a.id, b.id)] = 0.0
                    continue
                # Prune edges beyond max feasible distance
                if d_km > max_hop_km:
                    continue
                fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
                # Scale terrain sample count with distance (1 point per km, min 10)
                num_terrain_pts = max(10, int(d_km * 2))
                pending_edges.append((a.id, b.id, d_km, fspl, num_terrain_pts))
                pending_coros.append(
                    self.elevation.get_profile(
                        a.lat, a.lon, b.lat, b.lon, num_points=num_terrain_pts
                    )
                )

        # Fan-out: fetch all edge terrain profiles concurrently.
        profiles = await asyncio.gather(*pending_coros, return_exceptions=True) if pending_coros else []

        for (a_id, b_id, d_km, fspl, _npts), profile in zip(pending_edges, profiles):
            obstruction_penalty = 0.0
            if not isinstance(profile, Exception) and profile:
                try:
                    a_node = node_index[a_id]
                    b_node = node_index[b_id]
                    tx_h_asl = profile[0] + a_node.height_m
                    rx_h_asl = profile[-1] + b_node.height_m
                    clearance = LinkEngine.terrain_clearance(
                        profile, d_km, f_hz, tx_h_asl, rx_h_asl
                    )
                    if clearance < 0.6:
                        # Penalize obstructed hops: up to 20 dB extra loss
                        obstruction_penalty = (0.6 - clearance) * 33.0
                except Exception:
                    pass  # If terrain math fails, use FSPL only
            hop_cost[(a_id, b_id)] = fspl + obstruction_penalty

        # Build adjacency list from pre-computed costs for faster neighbor lookup
        adjacency: Dict[str, List[str]] = {t.id: [] for t in all_nodes}
        for (a_id, b_id) in hop_cost:
            adjacency[a_id].append(b_id)

        # Modified Dijkstra for bottleneck path:
        # cost = worst (max) single-hop effective loss along the path so far
        # We want to MINIMIZE this bottleneck cost.
        # Uses predecessor map instead of storing full path in heap.
        INF = float('inf')
        best: Dict[Tuple[str, int], float] = {}
        # predecessor: state -> (prev_node_id, prev_hops) for path reconstruction
        predecessor: Dict[Tuple[str, int], Optional[Tuple[str, int]]] = {}
        heap: list = [(0.0, 0, start_tower.id)]
        predecessor[(start_tower.id, 0)] = None
        result_state: Optional[Tuple[str, int]] = None
        result_cost = INF

        while heap:
            bottleneck, hops, nid = heapq.heappop(heap)

            if nid == "__target__":
                if bottleneck < result_cost:
                    result_cost = bottleneck
                    result_state = (nid, hops)
                continue

            if hops >= max_hops:
                continue

            state_key = (nid, hops)
            if state_key in best and best[state_key] <= bottleneck:
                continue
            best[state_key] = bottleneck

            current = node_index[nid]
            for neighbor_id in adjacency[nid]:
                edge_key = (nid, neighbor_id)
                effective_loss = hop_cost[edge_key]
                hop_rssi = current.power_dbm + tx_gain + rx_gain - effective_loss
                if hop_rssi < -95:
                    continue
                new_bottleneck = max(bottleneck, effective_loss)
                new_state = (neighbor_id, hops + 1)
                if new_state not in best or best[new_state] > new_bottleneck:
                    predecessor[new_state] = state_key
                    heapq.heappush(heap, (new_bottleneck, hops + 1, neighbor_id))

        if result_state is None:
            # Fallback: direct path (infeasible, but reported for user awareness)
            return [start_tower]

        # Reconstruct path from predecessor map
        result_path: List[str] = []
        state = result_state
        while state is not None:
            result_path.append(state[0])
            state = predecessor.get(state)
        result_path.reverse()

        # Return tower objects (exclude virtual target)
        return [node_index[nid] for nid in result_path if nid != "__target__"]

    @staticmethod
    def _generate_candidates(start_tower: Tower, target_receiver: Receiver,
                             max_hops: int) -> List[Tower]:
        """Generate candidate repeater sites along and beside the path.

        Creates on-axis candidates evenly spaced along the great-circle path
        plus lateral offset candidates on each side.  The lateral offsets let
        the optimiser route *around* terrain obstacles instead of being
        confined to the direct line.
        """
        num_on_axis = max(max_hops * 2, 4)
        candidates: List[Tower] = []
        idx = 0

        dlat = target_receiver.lat - start_tower.lat
        dlon = target_receiver.lon - start_tower.lon
        path_len_km = LinkEngine.haversine_km(
            start_tower.lat, start_tower.lon,
            target_receiver.lat, target_receiver.lon,
        )
        # Perpendicular unit vector (90° rotation in lat/lon plane)
        norm = math.sqrt(dlat**2 + dlon**2) or 1e-9
        perp_lat = -dlon / norm
        perp_lon = dlat / norm
        # Lateral offset ~10% of path length (capped at 5 km equivalent)
        offset_deg = min(path_len_km * 0.10 / 111.0, 5.0 / 111.0)

        for i in range(1, num_on_axis + 1):
            frac = i / (num_on_axis + 1)
            center_lat = start_tower.lat + dlat * frac
            center_lon = start_tower.lon + dlon * frac

            # On-axis candidate
            idx += 1
            candidates.append(Tower(
                id=f"candidate_{idx}",
                lat=center_lat, lon=center_lon, height_m=40.0,
                operator=start_tower.operator, bands=start_tower.bands,
                power_dbm=43.0,
            ))
            # Left offset candidate
            idx += 1
            candidates.append(Tower(
                id=f"candidate_{idx}",
                lat=center_lat + perp_lat * offset_deg,
                lon=center_lon + perp_lon * offset_deg,
                height_m=40.0,
                operator=start_tower.operator, bands=start_tower.bands,
                power_dbm=43.0,
            ))
            # Right offset candidate
            idx += 1
            candidates.append(Tower(
                id=f"candidate_{idx}",
                lat=center_lat - perp_lat * offset_deg,
                lon=center_lon - perp_lon * offset_deg,
                height_m=40.0,
                operator=start_tower.operator, bands=start_tower.bands,
                power_dbm=43.0,
            ))
        return candidates

    async def close(self):
        await self.elevation.close()

# ------------------------------------------------------------
# API Key Authentication
# ------------------------------------------------------------

class Tier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"
    ULTRA = "ultra"

TIER_LIMITS = {
    Tier.FREE: {"requests_per_min": int(os.getenv("RATE_LIMIT_FREE", "10")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_FREE", "5")), "max_towers": 20, "max_batch_rows": 0},
    Tier.STARTER: {"requests_per_min": int(os.getenv("RATE_LIMIT_STARTER", "30")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_STARTER", "50")), "max_towers": 100, "max_batch_rows": 100},
    Tier.PRO: {"requests_per_min": int(os.getenv("RATE_LIMIT_PRO", "100")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_PRO", "500")), "max_towers": 500, "max_batch_rows": 2000},
    Tier.BUSINESS: {"requests_per_min": int(os.getenv("RATE_LIMIT_BUSINESS", "300")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_BUSINESS", "5000")), "max_towers": 2000, "max_batch_rows": 5000},
    Tier.ENTERPRISE: {"requests_per_min": int(os.getenv("RATE_LIMIT_ENTERPRISE", "1000")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_ENTERPRISE", "100000")), "max_towers": 10000, "max_batch_rows": 10000},
    Tier.ULTRA: {"requests_per_min": int(os.getenv("RATE_LIMIT_ULTRA", "5000")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_ULTRA", "1000000")), "max_towers": 50000, "max_batch_rows": 50000},
}

# In-memory API key store: key -> {"tier": Tier, "owner": str, "demo": bool}
# Override with VALID_API_KEYS env var: JSON dict {"key": "tier", ...}
# e.g. VALID_API_KEYS='{"prod-key-001":"pro","prod-key-002":"enterprise"}'
#
# Demo keys (public, rate-limited, no PDF, no AI) are rotated monthly via
# the DEMO_KEYS env var and ship a distinct prefix (`demo_`) so they can be
# safely revoked without touching real customer keys.
_DEFAULT_DEMO_KEYS = {
    "demo_ttp_free_2604": Tier.FREE,
    "demo_ttp_starter_2604": Tier.STARTER,
    "demo_ttp_pro_2604": Tier.PRO,
}
_raw_demo = os.getenv("DEMO_KEYS")
if _raw_demo:
    try:
        _DEFAULT_DEMO_KEYS = {k: Tier(v) for k, v in json.loads(_raw_demo).items()}
    except Exception:
        logger.exception("invalid DEMO_KEYS env var; using defaults")
_demo_api_keys: Dict[str, Dict] = {
    k: {"tier": t, "owner": f"demo_{t.value}", "demo": True}
    for k, t in _DEFAULT_DEMO_KEYS.items()
}

_env_keys_raw = os.getenv("VALID_API_KEYS")
if _env_keys_raw:
    _env_keys = json.loads(_env_keys_raw)
    API_KEYS: Dict[str, Dict] = {
        k: {"tier": Tier(v), "owner": k, "demo": False} for k, v in _env_keys.items()
    }
else:
    API_KEYS: Dict[str, Dict] = {}

# Demo keys are enabled by default in staging; production should set
# ENABLE_DEMO_KEYS=false once real customers are onboarded unless a
# "Try the API" button on the landing page still points at them.
if os.getenv("ENABLE_DEMO_KEYS", "true").lower() in ("1", "true", "yes"):
    API_KEYS.update(_demo_api_keys)

# Hard-capped rate limit for demo keys regardless of nominal tier, to prevent
# public scraping abuse. Overridable via DEMO_RATE_LIMIT env (rpm).
_DEMO_RATE_LIMIT_RPM = int(os.getenv("DEMO_RATE_LIMIT", "6"))

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---- Per-key tower creation counter ----
_towers_created_per_key: Dict[str, int] = {}

# ---- Per-key cumulative request counter (for usage portal) ----
_usage_counters: Dict[str, Dict] = {}

# ---- Per-key monthly PDF quota counter ----
# Backed by ``key_store_db`` (PostgreSQL when DATABASE_URL is set, in-memory
# fallback otherwise). The counter is keyed on (api_key, YYYY-MM) so it
# survives container restarts and rolling deploys.
import key_store_db as _key_store_db

def _enforce_pdf_quota(api_key: str, tier: Tier) -> int:
    """Raise 429 if the caller exceeds the monthly PDF quota for their tier.
    Returns the new count after increment."""
    limits = TIER_LIMITS[tier]
    quota = limits.get("pdf_per_month")
    if quota is None:
        return 0
    period = time.strftime("%Y-%m", time.gmtime())
    try:
        return _key_store_db.consume_pdf_quota(api_key, period, int(quota))
    except _key_store_db.QuotaExceeded:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Monthly PDF quota exceeded ({quota} PDFs/month for {tier.value} tier). "
                "Upgrade your plan at https://app.telecomtowerpower.com.br/pricing"
            ),
        )

def _track_usage(api_key: str):
    """Increment per-key request counter."""
    entry = _usage_counters.setdefault(api_key, {"requests": 0, "since": time.time()})
    entry["requests"] += 1

# ---- Sliding-window rate limiter (per API key) ----
_rate_buckets: Dict[str, collections.deque] = {}

def _check_rate_limit(api_key: str, tier: Tier, is_demo: bool = False) -> Tuple[int, int]:
    """Raise 429 if the caller exceeds their tier's requests_per_min.
    Demo keys are additionally capped at `DEMO_RATE_LIMIT` rpm regardless
    of nominal tier, to prevent public scraping abuse.
    Admin keys (members of ``ADMIN_API_KEYS``) bypass the limiter.
    Returns (remaining, limit) for response headers."""
    # Admin keys are unmetered: support / impersonation calls must not
    # 429 the operator. Returning a large sentinel keeps response headers
    # well-formed without leaking that the caller is privileged.
    if api_key in _ADMIN_API_KEYS:
        return 999_999, 999_999
    limit = TIER_LIMITS[tier]["requests_per_min"]
    if is_demo:
        limit = min(limit, _DEMO_RATE_LIMIT_RPM)
    now = time.monotonic()
    window = 60.0  # seconds

    bucket = _rate_buckets.setdefault(api_key, collections.deque())
    # Evict timestamps older than the window
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        RATE_LIMIT_HITS.labels(tier=tier.value).inc()
        _record_rate_limit_hit(tier.value)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} requests/min for {tier.value} tier). "
                   "Try again shortly.",
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    bucket.append(now)
    remaining = max(0, limit - len(bucket))
    return remaining, limit

def _caller_owner(request: Request, key_data: Dict) -> str:
    """Return a stable, non-empty *owner* string identifying the caller for
    OWASP A01 (broken-object-level-authorization) checks on tenant-scoped
    rows. Prefers ``key_data['owner']`` (set for Stripe-provisioned keys);
    falls back to a SHA-256 fingerprint of the raw API key so static keys
    are still tenant-isolated without persisting raw secrets in row data.
    Never returns ``"system"`` (reserved for shared/public datasets).
    """
    owner = key_data.get("owner") if isinstance(key_data, dict) else None
    if owner and owner != "system":
        return str(owner)
    raw_key = request.headers.get("x-api-key", "") or ""
    if not raw_key:
        return "anonymous"
    return "key:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


async def verify_api_key(request: Request, api_key: str = Security(api_key_header)) -> Dict:
    """Validate the API key, enforce rate limit, and return key metadata."""
    # ── SSO Bearer fallback ─────────────────────────────────────────────
    # If no X-API-Key was supplied, try Authorization: Bearer <id_token>.
    # Successful verification is mapped to the api_key row stamped with
    # the IdP's (provider, sub) pair via key_store_db.lookup_by_oauth.
    if not api_key:
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            parts = auth_header.split(None, 1)
            token = parts[1].strip() if len(parts) > 1 else ""
            if not token:
                raise HTTPException(status_code=401, detail="Invalid or missing API key")
            try:
                claims = _sso.verify_id_token(token, provider="cognito")
            except _sso.SsoTokenError as exc:
                logger.info("sso bearer rejected: %s", exc)
                raise HTTPException(status_code=401, detail="Invalid SSO token")
            try:
                import key_store_db as _ksd
                mapped = _ksd.lookup_by_oauth("cognito", str(claims["sub"]))
            except Exception:  # noqa: BLE001
                logger.exception("sso lookup_by_oauth failed")
                mapped = None
            if not mapped or not mapped.get("api_key"):
                # Token is valid but never exchanged via /auth/sso.
                raise HTTPException(
                    status_code=401,
                    detail="SSO identity not provisioned. Call POST /auth/sso first.",
                )
            api_key = mapped["api_key"]
        else:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    key_data = API_KEYS.get(api_key)
    if key_data is None:
        # Check dynamically-registered keys from Stripe billing
        dynamic = stripe_billing.lookup_key(api_key)
        if dynamic is not None:
            key_data = {"tier": Tier(dynamic["tier"]), "owner": dynamic["owner"], "demo": False}
        else:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    remaining, limit = _check_rate_limit(api_key, key_data["tier"], is_demo=bool(key_data.get("demo")))
    _track_usage(api_key)
    request.state.tier = key_data["tier"].value
    request.state.rate_limit_remaining = remaining
    request.state.rate_limit_limit = limit
    request.state.is_demo = bool(key_data.get("demo"))
    # Mounted sub-routers (e.g. rf_engines_router) inherit verify_api_key
    # via FastAPI's `dependencies=[…]` form, which doesn't pass key_data
    # to handlers. Surface the owner/api_key on request.state so those
    # handlers can enforce tier + IDOR without a circular import.
    request.state.api_key = api_key
    request.state.owner = key_data.get("owner")
    # Carry the api_key in the dependency payload so downstream handlers
    # (tenant branding, usage portal) can look up per-key state without
    # re-parsing the header.
    key_data = dict(key_data)
    key_data["api_key"] = api_key
    # Admin keys (members of ``ADMIN_API_KEYS``) get a flag that downstream
    # tier-gates honor. Their actions are still audited like any other tenant.
    if api_key in _ADMIN_API_KEYS:
        key_data["is_admin"] = True
        request.state.is_admin = True
    return key_data

def require_tier(*allowed: Tier):
    """Dependency that checks the caller's tier against allowed tiers."""
    async def _check(key_data: Dict = Depends(verify_api_key)):
        # Admin keys bypass tier gates so support / impersonation calls
        # work against any endpoint regardless of the admin's nominal tier.
        if key_data.get("is_admin"):
            return key_data
        if key_data["tier"] not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"This endpoint requires one of: {[t.value for t in allowed]}. "
                       f"Your tier: {key_data['tier'].value}"
            )
        return key_data
    return _check

# ------------------------------------------------------------
# Configuration from environment
# ------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))  # 10 MB
MAX_BATCH_ROWS = int(os.getenv("MAX_BATCH_ROWS", "5000"))

_DEFAULT_ORIGINS_PROD = (
    "https://www.telecomtowerpower.com.br,"
    "https://telecomtowerpower.com.br,"
    "https://app.telecomtowerpower.com.br,"
    "https://api.telecomtowerpower.com.br,"
    "https://app.telecomtowerpower.com,"
    "https://dashboard.telecomtowerpower.com,"
    "https://frontend-production-3542d.up.railway.app"
)
_DEFAULT_ORIGINS_DEV = (
    _DEFAULT_ORIGINS_PROD
    + ",http://localhost:3000,http://localhost:8000"
)
# SECURITY: never expose the public API to http://localhost:* origins in
# production — that would make any locally-running attacker page able to
# call the API with the user's browser credentials. Localhost origins are
# only allowed when APP_ENV is unset, "dev", "development", or "test".
_APP_ENV = os.getenv("APP_ENV", "").strip().lower()
_IS_PROD = _APP_ENV in ("production", "prod")
_DEFAULT_ORIGINS = _DEFAULT_ORIGINS_PROD if _IS_PROD else _DEFAULT_ORIGINS_DEV

_allowed_origins_raw = os.getenv("CORS_ORIGINS", _DEFAULT_ORIGINS)
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]
if _IS_PROD:
    _bad = [o for o in _allowed_origins if "localhost" in o or "127.0.0.1" in o]
    if _bad:
        # Refuse to ship a config that would CSRF-expose us. Operator must
        # remove these or set APP_ENV to a non-prod value.
        raise RuntimeError(
            f"CORS_ORIGINS contains localhost entries in production: {_bad}. "
            "Remove them or set APP_ENV=dev for local testing."
        )

# ------------------------------------------------------------
# FastAPI application
# ------------------------------------------------------------

app = FastAPI(
    title="TELECOM TOWER POWER API",
    description="Cell tower coverage, link analysis, and repeater planning. "
                "Requires an API key via the `X-API-Key` header.",
)


# Map OfflineModeError → HTTP 503 so paid third-party features
# (Stripe checkout, Stripe webhook) degrade cleanly when the operator
# has set TTP_OFFLINE=1 (air-gapped install / on-prem).
@app.exception_handler(OfflineModeError)
async def _offline_mode_handler(_request, exc: OfflineModeError):  # noqa: ANN001
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "feature": exc.feature,
            "offline": True,
        },
    )

# Distributed tracing (OpenTelemetry -> Jaeger). No-op unless OTEL_ENABLED=true.
try:
    from tracing import setup_tracing as _setup_tracing
    _setup_tracing(app)
except Exception:
    logger.exception("tracing setup failed; continuing without tracing")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    expose_headers=["X-RateLimit-Remaining", "X-RateLimit-Limit", "X-Demo-Key", "X-Demo-Notice"],
)

# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=()"
    response.headers["Cache-Control"] = "no-store"
    # HSTS: tell browsers to always use HTTPS (1 year, include subdomains)
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains; preload"
    )
    return response

# Request body size limit middleware
@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        return Response(
            content=json.dumps({
                "detail": f"Request body too large. Maximum size is "
                           f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
            }),
            status_code=413,
            media_type="application/json",
        )
    return await call_next(request)

# Prometheus latency / count middleware
@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    endpoint = request.url.path
    tier = getattr(request.state, "tier", "anonymous")
    REQUEST_LATENCY.labels(
        method=request.method, endpoint=endpoint, status=response.status_code, tier=tier,
    ).observe(elapsed)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=response.status_code, tier=tier,
    ).inc()
    # Inject rate-limit headers when auth ran successfully
    rl_remaining = getattr(request.state, "rate_limit_remaining", None)
    if rl_remaining is not None:
        response.headers["X-RateLimit-Remaining"] = str(rl_remaining)
        response.headers["X-RateLimit-Limit"] = str(
            getattr(request.state, "rate_limit_limit", 0)
        )
    if getattr(request.state, "is_demo", False):
        response.headers["X-Demo-Key"] = "true"
        response.headers["X-Demo-Notice"] = (
            "Public demo key in use; do not use for production. "
            "Sign up at https://app.telecomtowerpower.com.br/signup"
        )
    logger.info(
        "request",
        extra={
            "http_method": request.method,
            "path": endpoint,
            "status": response.status_code,
            "duration_ms": round(elapsed * 1000, 2),
            "api_key_tier": tier,
        },
    )
    if response.status_code >= 500:
        _alert_slack(
            f"`{request.method} {endpoint}` returned *{response.status_code}* "
            f"({round(elapsed * 1000)}ms, tier={tier})"
        )
    return response

# Global platform instance
platform = TelecomTowerPower()
job_store = JobStore()

# ── SQS client for async batch pipeline ──
# Two queues: default (Starter/Pro/Business) and high-priority (Enterprise).
# Falls back to the default queue if the priority URL is unset, so single-queue
# deployments keep working unchanged.
_sqs_client = None
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
SQS_QUEUE_URL_PRIORITY = os.getenv("SQS_QUEUE_URL_PRIORITY", "")


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def _queue_for_tier(tier_value: str) -> str:
    """Pick the SQS queue URL for a given tier. Enterprise/Ultra → priority queue."""
    if tier_value in ("enterprise", "ultra") and SQS_QUEUE_URL_PRIORITY:
        return SQS_QUEUE_URL_PRIORITY
    return SQS_QUEUE_URL


# ── AWS Batch (T19) ─────────────────────────────────────────────
# GPU-backed interference jobs (engine=sionna-rt) go to AWS Batch
# instead of the SQS Lambda — the ray-tracing scene + mitsuba GPU
# stack don't fit the Lambda runtime envelope.
_batch_client = None
BATCH_JOB_QUEUE_GPU = os.getenv("BATCH_JOB_QUEUE_GPU", "")
BATCH_JOB_DEFINITION_GPU = os.getenv("BATCH_JOB_DEFINITION_GPU", "")


def _get_batch():
    global _batch_client
    if _batch_client is None:
        import boto3
        _batch_client = boto3.client("batch")
    return _batch_client


def _submit_gpu_batch_job(job_id: str, tier: str) -> str:
    """Submit one async interference job to the GPU AWS Batch queue.

    Returns the AWS Batch ``jobId``. Raises HTTPException(503) if the
    deployment hasn't provisioned the queue + job definition yet —
    we fail closed rather than silently downgrading to FSPL because
    the caller explicitly asked for sionna-rt.
    """
    if not BATCH_JOB_QUEUE_GPU or not BATCH_JOB_DEFINITION_GPU:
        raise HTTPException(
            status_code=503,
            detail="GPU Batch backend not provisioned: set "
                   "BATCH_JOB_QUEUE_GPU and BATCH_JOB_DEFINITION_GPU "
                   "env vars on the API task. engine='sionna-rt' "
                   "cannot run without it.",
        )
    # Batch job names: alphanumerics + hyphens, max 128 chars.
    safe_name = f"interference-rt-{job_id}".replace("_", "-")[:128]
    resp = _get_batch().submit_job(
        jobName=safe_name,
        jobQueue=BATCH_JOB_QUEUE_GPU,
        jobDefinition=BATCH_JOB_DEFINITION_GPU,
        containerOverrides={
            "command": [
                "python", "-m", "batch_gpu_interference_worker",
                job_id, tier or "",
            ],
            "environment": [
                {"name": "JOB_ID", "value": job_id},
                {"name": "JOB_TIER", "value": tier or ""},
            ],
        },
    )
    return str(resp.get("jobId", ""))

# Pydantic models for API
class TowerInput(BaseModel):
    id: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = Field(..., gt=0)
    operator: str
    bands: List[Band] = Field(..., min_length=1)
    power_dbm: float = 43.0

class ReceiverInput(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = 10.0
    antenna_gain_dbi: float = 12.0

class LinkAnalysisResponse(BaseModel):
    feasible: bool
    signal_dbm: float
    fresnel_clearance: float
    los_ok: bool
    distance_km: float
    recommendation: str
    terrain_profile: Optional[List[float]] = None
    tx_height_asl: Optional[float] = None
    rx_height_asl: Optional[float] = None

@app.get("/")
async def root():
    """Health check and API overview."""
    return {
        "service": "TELECOM TOWER POWER API",
        "status": "online",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": [
            "/towers", "/towers/{id}", "/towers/nearest",
            "/analyze", "/plan_repeater", "/coverage/predict",
            "/export_report", "/export_report/pdf",
            "/batch_reports", "/health",
        ],
    }

@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint."""
    try:
        import hop_cache  # noqa: PLC0415
        for op, n in hop_cache.get_metrics().items():
            HOP_CACHE_OPS.labels(op=op).set(n)
    except Exception:  # noqa: BLE001
        logger.debug("hop_cache metrics unavailable", exc_info=True)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health")
async def health_check():
    """Lightweight liveness / readiness probe for load balancers."""
    srtm_ok = os.path.isdir(platform.elevation.srtm.data_dir)
    tower_count = platform.tower_count()
    queued_jobs = len(job_store.list_jobs(status="queued"))
    running_jobs = len(job_store.list_jobs(status="running"))
    return {
        "status": "healthy",
        "towers_in_db": tower_count,
        "db_backend": platform.db.backend,
        "jobs_queued": queued_jobs,
        "jobs_running": running_jobs,
        "elevation_cache_size": len(platform.elevation.cache),
        "srtm_available": srtm_ok,
        "offline": is_offline(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/debug/error500", include_in_schema=False)
async def debug_error500():
    """Return a 500 error for testing Prometheus alert rules.

    Only available when ENABLE_DEBUG_ENDPOINTS=true (disabled by default).
    """
    if os.getenv("ENABLE_DEBUG_ENDPOINTS", "false").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not found")
    raise HTTPException(status_code=500, detail="Synthetic 500 for alert testing")

_reaper_shutdown = asyncio.Event()
_reaper_task = None
_queue_metrics_task = None

# ------------------------------------------------------------
# Per-tier rolling rate-limit hit-rate tracking (1-minute window).
# Used to populate the rate_limit_hit_rate_per_minute gauge without
# relying solely on PromQL rate() over the counter.
# ------------------------------------------------------------
_rl_hit_timestamps: Dict[str, collections.deque] = {}

def _record_rate_limit_hit(tier_value: str) -> None:
    """Record a rate-limit rejection for the given tier and refresh the gauge."""
    now = time.monotonic()
    bucket = _rl_hit_timestamps.setdefault(tier_value, collections.deque())
    bucket.append(now)
    cutoff = now - 60.0
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    RATE_LIMIT_HIT_RATE.labels(tier=tier_value).set(float(len(bucket)))


async def _batch_queue_metrics_updater(interval_seconds: int = 15) -> None:
    """Periodically sample the batch queue and update Prometheus gauges.

    Populates `batch_queue_depth{status=...}`, `batch_queue_oldest_age_seconds`,
    and decays the per-tier rate-limit hit-rate gauge so that the value falls
    back to 0 once a tier stops hitting its limit.
    """
    while not _reaper_shutdown.is_set():
        try:
            await asyncio.wait_for(_reaper_shutdown.wait(), timeout=interval_seconds)
            break  # shutdown signalled
        except asyncio.TimeoutError:
            pass
        try:
            # Fetch a generous page of recent jobs to approximate depth by status.
            # For deeper queues this undercounts — acceptable for alerting since
            # the alert also keys off the oldest-age gauge (which uses ORDER BY).
            queued = job_store.list_jobs(status="queued", limit=1000)
            running = job_store.list_jobs(status="running", limit=1000)
            failed = job_store.list_jobs(status="failed", limit=1000)
            BATCH_QUEUE_DEPTH.labels(status="queued").set(len(queued))
            BATCH_QUEUE_DEPTH.labels(status="running").set(len(running))
            BATCH_QUEUE_DEPTH.labels(status="failed").set(len(failed))
            BATCH_JOBS_ACTIVE.set(len(running))
            if queued:
                # list_jobs returns newest first; oldest is last.
                oldest = min(float(j.get("created_at") or 0.0) for j in queued)
                age = max(0.0, time.time() - oldest) if oldest else 0.0
                BATCH_QUEUE_OLDEST_AGE.set(age)
            else:
                BATCH_QUEUE_OLDEST_AGE.set(0.0)
        except Exception:
            logger.exception("Batch queue metrics updater failed")
        # Decay the rate-limit hit-rate gauge: drop events older than 60s.
        try:
            now = time.monotonic()
            cutoff = now - 60.0
            for tier_value, bucket in _rl_hit_timestamps.items():
                while bucket and bucket[0] < cutoff:
                    bucket.popleft()
                RATE_LIMIT_HIT_RATE.labels(tier=tier_value).set(float(len(bucket)))
        except Exception:
            logger.exception("Rate-limit gauge decay failed")

async def _stale_job_reaper(interval_seconds: int = 60,
                            max_age_seconds: int = 300) -> None:
    """Periodically release running jobs whose worker has stopped heartbeating.

    Runs as a background asyncio task for the lifetime of the API process.
    Jobs are reset to 'queued' (not failed) so another worker can retry them.
    """
    while not _reaper_shutdown.is_set():
        try:
            await asyncio.wait_for(_reaper_shutdown.wait(), timeout=interval_seconds)
            break  # shutdown signalled
        except asyncio.TimeoutError:
            pass  # normal timeout — do reaper work
        try:
            released = job_store.release_stale_jobs(max_age_seconds=max_age_seconds)
            if released:
                STALE_JOBS_REAPED.inc(released)
                logger.warning(
                    "Stale job reaper: released %d job(s) back to queue "
                    "(no heartbeat for >%ds)",
                    released, max_age_seconds,
                )
                _alert_slack(
                    f":recycle: Released {released} stale running job(s) back to queue "
                    f"(no heartbeat for >{max_age_seconds}s)"
                )
        except Exception:
            logger.exception("Stale job reaper encountered an error")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ── Security: validate critical config at boot ────────────────
    # 1. Stripe webhook secret must be set whenever a Stripe API key is
    #    set, otherwise webhooks would be processed without signature
    #    verification (or rejected unexpectedly in prod). Refuse to boot.
    try:
        import stripe_billing as _sb
        if _sb.STRIPE_SECRET_KEY and not _sb.STRIPE_WEBHOOK_SECRET:
            raise RuntimeError(
                "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is missing — "
                "webhook signature verification would fail. Refusing to start."
            )
    except RuntimeError:
        raise
    except Exception:
        logger.exception("Stripe config validation skipped (module unavailable)")

    # 2. SAGEMAKER_COVERAGE_ENDPOINT, when set, must be a syntactically
    #    valid SageMaker endpoint name (1-63 chars, [A-Za-z0-9-]). This
    #    blocks an attacker from coaxing the service into invoking an
    #    arbitrary endpoint via a poisoned env var or SSM param.
    _sm_endpoint = os.getenv("SAGEMAKER_COVERAGE_ENDPOINT", "").strip()
    if _sm_endpoint and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", _sm_endpoint):
        raise RuntimeError(
            f"SAGEMAKER_COVERAGE_ENDPOINT={_sm_endpoint!r} is not a valid "
            "SageMaker endpoint name (must match [A-Za-z0-9][A-Za-z0-9-]{0,62})."
        )

    # Fail stale running jobs from previous deploys (one-time cleanup on boot)
    try:
        recovered = job_store.fail_stale_jobs(max_age_seconds=600)
        if recovered:
            _alert_slack(f":warning: Recovered {recovered} stale running job(s) on startup")
    except Exception:
        pass
    tower_count = platform.db.count()
    _alert_slack(f":white_check_mark: API started — {tower_count:,} towers in DB ({platform.db.backend})")

    # Kick off background SRTM tile prefetch for the configured country so
    # true-cold regions don't pin a worker on slow Open-Elevation calls.
    # Honors SRTM_PREFETCH_COUNTRY (ISO-2) or disables when unset/empty.
    prefetch_country = os.getenv("SRTM_PREFETCH_COUNTRY", "").strip().upper()
    if prefetch_country:
        try:
            from srtm_prefetch import prefetch_country_async
            prefetch_country_async(prefetch_country)
            logger.info("SRTM background prefetch scheduled for %s", prefetch_country)
        except Exception:
            logger.exception("failed to schedule SRTM prefetch for %s", prefetch_country)

    # Launch background reaper: releases jobs with no heartbeat back to queue
    global _reaper_task
    _reaper_interval = int(os.getenv("STALE_JOB_REAPER_INTERVAL", "60"))
    _stale_job_timeout = int(os.getenv("STALE_JOB_TIMEOUT", "300"))
    _reaper_task = asyncio.create_task(
        _stale_job_reaper(
            interval_seconds=_reaper_interval,
            max_age_seconds=_stale_job_timeout,
        ),
        name="stale-job-reaper",
    )

    # Launch background queue-metrics updater: keeps batch_queue_depth and
    # batch_queue_oldest_age_seconds gauges fresh for Prometheus scrapes.
    global _queue_metrics_task
    _queue_metrics_interval = int(os.getenv("QUEUE_METRICS_INTERVAL", "15"))
    _queue_metrics_task = asyncio.create_task(
        _batch_queue_metrics_updater(interval_seconds=_queue_metrics_interval),
        name="batch-queue-metrics-updater",
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────
    _reaper_shutdown.set()
    if _reaper_task:
        _reaper_task.cancel()
        try:
            await _reaper_task
        except asyncio.CancelledError:
            pass
    if _queue_metrics_task:
        _queue_metrics_task.cancel()
        try:
            await _queue_metrics_task
        except asyncio.CancelledError:
            pass
    await platform.close()


# Wire the lifespan after definition (avoids forward-reference at FastAPI() construction).
app.router.lifespan_context = _lifespan


@app.post("/towers", status_code=201)
async def add_tower(request: Request, tower: TowerInput, key_data: Dict = Depends(verify_api_key)):
    """Add a new tower to the database."""
    tier_limit = TIER_LIMITS[key_data["tier"]]["max_towers"]
    api_key = key_data.get("owner", "unknown")
    created = _towers_created_per_key.get(api_key, 0)
    if created >= tier_limit:
        raise HTTPException(status_code=403, detail=f"Tower creation limit reached for {key_data['tier'].value} tier ({tier_limit})")
    owner = _caller_owner(request, key_data)
    new_tower = Tower(
        id=tower.id,
        lat=tower.lat,
        lon=tower.lon,
        height_m=tower.height_m,
        operator=tower.operator,
        bands=tower.bands,
        power_dbm=tower.power_dbm,
        owner=owner,
    )
    platform.add_tower(new_tower)
    _towers_created_per_key[api_key] = created + 1
    return {"message": f"Tower {tower.id} added"}

@app.get("/towers/nearest")
async def nearest_towers(request: Request, lat: float, lon: float, operator: Optional[str] = None, limit: int = 5, key_data: Dict = Depends(verify_api_key)):
    """Find nearest towers to a given location."""
    owner = _caller_owner(request, key_data)
    nearest = platform.find_nearest_towers(lat, lon, operator, limit, owner=owner)
    results = []
    for t in nearest:
        d = asdict(t)
        d["distance_km"] = round(LinkEngine.haversine_km(lat, lon, t.lat, t.lon), 3)
        results.append(d)
    return {"nearest_towers": results}

@app.get("/towers/{tower_id}")
async def get_tower(request: Request, tower_id: str, key_data: Dict = Depends(verify_api_key)):
    """Get a single tower by ID."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    # OWASP A01: deny cross-tenant reads. System-owned rows (Anatel,
    # OpenCellID imports …) remain readable by any authenticated caller.
    owner = _caller_owner(request, key_data)
    if tower.owner not in (owner, "system"):
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    return asdict(tower)

@app.get("/towers")
async def list_towers(
    request: Request,
    operator: Optional[str] = None,
    limit: int = Query(default=1000, ge=1, le=50000),
    offset: int = Query(default=0, ge=0),
    key_data: Dict = Depends(verify_api_key),
):
    """List towers with pagination. Use *offset* and *limit* to page through results.

    Tenants see system-owned (public dataset) rows plus their own creations.
    Cross-tenant rows are filtered out at the SQL layer.
    """
    owner = _caller_owner(request, key_data)
    towers_list = platform.list_towers(operator=operator, limit=limit, offset=offset, owner=owner)
    total = platform.tower_count()
    return {"towers": [asdict(t) for t in towers_list], "total": total, "offset": offset, "limit": limit}

@app.put("/towers/{tower_id}")
async def update_tower(request: Request, tower_id: str, tower: TowerInput, key_data: Dict = Depends(verify_api_key)):
    """Update an existing tower.  The tower ID in the path must match the body."""
    if tower.id != tower_id:
        raise HTTPException(status_code=400, detail="Tower ID in path and body must match")
    existing = platform.get_tower(tower_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    # OWASP A01: only the owner may mutate. System-owned rows are read-only
    # to tenants (return 404 to avoid disclosing existence to non-owners).
    owner = _caller_owner(request, key_data)
    if existing.owner != owner:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    updated = Tower(
        id=tower.id, lat=tower.lat, lon=tower.lon,
        height_m=tower.height_m, operator=tower.operator,
        bands=tower.bands, power_dbm=tower.power_dbm,
        owner=owner,
    )
    platform.update_tower(updated)
    return {"message": f"Tower {tower_id} updated"}

@app.delete("/towers/{tower_id}")
async def delete_tower(request: Request, tower_id: str, key_data: Dict = Depends(verify_api_key)):
    """Delete a tower from the database."""
    # OWASP A01: scope DELETE by owner so tenants can never remove
    # system-owned or other tenants' rows.
    owner = _caller_owner(request, key_data)
    if not platform.remove_tower(tower_id, owner=owner):
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    return {"message": f"Tower {tower_id} deleted"}

@app.post("/analyze", response_model=LinkAnalysisResponse)
async def analyze_link(tower_id: str, receiver: ReceiverInput, key_data: Dict = Depends(verify_api_key)):
    """
    Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.
    """
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.model_dump())
    result = await platform.analyze_link(tower, rx, terrain_profile=None)
    return LinkAnalysisResponse(**asdict(result))


# ─────────────────────────────────────────────────────────────────────
# /coverage/predict – ML-based signal prediction (Pro / Business / Enterprise)
# ─────────────────────────────────────────────────────────────────────

class CoveragePredictRequest(BaseModel):
    """Request body for /coverage/predict.

    Provide either ``tower_id`` (existing tower) **or** the explicit
    ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
    Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
    bounding box (``bbox``) to compute a coverage grid.
    """

    # Transmitter selection
    tower_id: Optional[str] = None
    tx_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    tx_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    tx_height_m: Optional[float] = Field(default=None, gt=0)
    tx_power_dbm: float = 43.0
    tx_gain_dbi: float = 17.0
    band: Optional[Band] = None

    # Receiver – either a point ...
    rx_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    rx_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    rx_height_m: float = 10.0
    rx_gain_dbi: float = 12.0

    # ... or a bbox for grid coverage maps
    bbox: Optional[List[float]] = Field(
        default=None,
        description="[min_lat, min_lon, max_lat, max_lon] for grid mode",
        min_length=4,
        max_length=4,
    )
    grid_size: int = Field(default=20, ge=2, le=200)
    # Real-time AI heatmap: cell resolution in metres. When set, ``grid_size``
    # is derived from the bbox and clamped per tier.
    cell_size_m: Optional[float] = Field(
        default=None,
        gt=0,
        le=10_000,
        description="Cell size in metres (e.g. 50 for a 50x50 m heatmap). "
                    "Overrides grid_size when supplied.",
    )

    feasibility_threshold_dbm: float = -95.0
    explain: bool = False

    # Predictor selector: see ``coverage_predict.predict_signal``.
    # "auto" (default) blends ridge + ITU-R P.1812 when both are
    # available; "ml" forces the ridge / band-aware path; "itu" forces
    # P.1812 physics; "hybrid" is an explicit alias of auto with both
    # predictors required.
    model: Literal["auto", "ml", "itu", "hybrid"] = "auto"

    # Engine selector.
    # - ``"auto"`` (default) runs the synchronous ML/physics path.
    #   **Auto-promotion**: on ENTERPRISE / ULTRA tiers, when ``bbox``
    #   and ``scene_s3_uri`` are both provided, ``"auto"`` is silently
    #   upgraded to ``"sionna_rt"`` so Tier-1 clients get ray-tracing
    #   without having to change their request body.
    # - ``"sionna_rt"`` / ``"sionna-rt"`` (equivalent) enqueue an
    #   asynchronous GPU ray-tracing job on AWS Batch and return
    #   HTTP 202 + ``job_id``; the client polls ``poll_url`` for the
    #   result.  Requires bbox (grid mode) and ``scene_s3_uri``.
    engine: Literal["auto", "sionna_rt", "sionna-rt"] = "auto"
    scene_s3_uri: Optional[str] = Field(
        default=None,
        description="s3:// URI of the Mitsuba scene bundle. Required "
                    "when engine='sionna_rt' / 'sionna-rt'.",
    )
    report_format: Literal["json", "pdf"] = Field(
        default="json",
        description="Response format. 'pdf' returns an engineering report rendered with WeasyPrint.",
    )


@app.post("/coverage/predict")
async def coverage_predict(
    request: Request,
    body: CoveragePredictRequest,
    _key: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """ML-based signal coverage prediction.

    Uses a terrain-aware regression model trained on SRTM elevation
    features. Routes to a SageMaker endpoint when
    ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
    locally-trained model, with a deterministic physics fallback when
    no model artefact is available.

    Modes:
    - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
    - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.
    - **ray-tracing** — set ``engine='sionna_rt'`` (or ``'sionna-rt'``)
      with ``bbox`` and ``scene_s3_uri``; returns HTTP 202 + job_id.
      ENTERPRISE / ULTRA tiers are promoted automatically when both
      ``bbox`` and ``scene_s3_uri`` are supplied with ``engine='auto'``.

    Restricted to Pro / Business / Enterprise / Ultra tiers.
    """
    # Lazy import to keep cold-start cost off endpoints that don't use ML.
    import coverage_predict as _cp

    if body.report_format == "pdf":
        raw_key = request.headers.get("x-api-key", "") or ""
        _enforce_pdf_quota(raw_key, _key["tier"])

    # ── Tier + effective engine ────────────────────────────────────
    caller_tier: Tier = _key["tier"]
    # Normalise hyphen variant to underscore canonical form.
    requested_engine = body.engine.replace("-", "_")
    # Auto-promote: ENTERPRISE / ULTRA with bbox + scene_s3_uri → sionna_rt.
    if (
        requested_engine == "auto"
        and caller_tier in (Tier.ENTERPRISE, Tier.ULTRA)
        and body.bbox is not None
        and body.scene_s3_uri
    ):
        effective_engine = "sionna_rt"
    else:
        effective_engine = requested_engine
    if body.tower_id:
        tower = platform.get_tower(body.tower_id)
        if not tower:
            raise HTTPException(status_code=404, detail=f"Tower {body.tower_id} not found")
        tx_lat = tower.lat
        tx_lon = tower.lon
        tx_h = tower.height_m
        tx_power = tower.power_dbm
        f_hz = tower.primary_freq_hz()
    else:
        if (
            body.tx_lat is None or body.tx_lon is None
            or body.tx_height_m is None or body.band is None
        ):
            raise HTTPException(
                status_code=422,
                detail="Provide either tower_id or tx_lat/tx_lon/tx_height_m/band",
            )
        tx_lat = body.tx_lat
        tx_lon = body.tx_lon
        tx_h = body.tx_height_m
        tx_power = body.tx_power_dbm
        f_hz = body.band.to_hz()

    # ── Sionna RT (async GPU) ──────────────────────────────────────
    # Delegates to the existing async raster pipeline. We translate
    # the simpler ``CoveragePredictRequest`` shape into the canonical
    # ``SionnaRTRasterRequest`` and call the same handler so tier
    # gating, cell caps, SQS, S3 polling and audit logging stay in
    # exactly one place.
    if effective_engine == "sionna_rt":
        if body.bbox is None:
            raise HTTPException(
                status_code=422,
                detail="engine='sionna_rt' requires bbox (grid mode); "
                       "single-point RT is not supported",
            )
        if not body.scene_s3_uri:
            raise HTTPException(
                status_code=422,
                detail="engine='sionna_rt' requires scene_s3_uri "
                       "(prebuilt Mitsuba scene bundle on S3)",
            )
        # CoveragePredictRequest bbox order is [min_lat, min_lon, max_lat, max_lon].
        # Sionna router uses [south, west, north, east] — same ordering.
        from rf_engines_router import (
            SionnaRTRasterRequest as _RtReq,
            _RasterGridIn as _RtGrid,
            _TxIn as _RtTx,
            sionna_rt_raster_submit as _rt_submit,
        )
        rt_req = _RtReq(
            scene_s3_uri=body.scene_s3_uri,
            tx=_RtTx(lat=tx_lat, lon=tx_lon, height_m=tx_h, power_dbm=tx_power),
            frequency_hz=f_hz,
            raster_grid=_RtGrid(
                rows=body.grid_size,
                cols=body.grid_size,
                bbox=list(body.bbox),
            ),
        )
        accepted = await _rt_submit(rt_req, request)
        accepted_payload = accepted.model_dump()
        if body.report_format == "pdf":
            request_payload = body.model_dump(mode="json")
            request_payload["tier"] = _key["tier"].value
            pdf_buffer = render_coverage_predict_pdf(request_payload, accepted_payload)
            filename = f"coverage_predict_{accepted_payload.get('job_id', 'queued')}.pdf"
            return StreamingResponse(
                pdf_buffer,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
                status_code=200,
            )
        return JSONResponse(status_code=202, content=accepted_payload)

    # ── Grid mode ──────────────────────────────────────────────────
    if body.bbox is not None:
        try:
            grid = await _cp.predict_coverage_grid(
                tx_lat=tx_lat,
                tx_lon=tx_lon,
                tx_h_m=tx_h,
                f_hz=f_hz,
                bbox=tuple(body.bbox),
                grid_size=body.grid_size,
                rx_h_m=body.rx_height_m,
                tx_power_dbm=tx_power,
                tx_gain_dbi=body.tx_gain_dbi,
                rx_gain_dbi=body.rx_gain_dbi,
                elevation_service=platform.elevation,
                feasibility_threshold_dbm=body.feasibility_threshold_dbm,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        signals = [p.signal_dbm for p in grid]
        feasible_pct = round(100.0 * sum(1 for p in grid if p.feasible) / len(grid), 1)
        model = _cp.get_model()
        payload = {
            "mode": "grid",
            "engine_used": effective_engine,
            "tx": {"lat": tx_lat, "lon": tx_lon, "height_m": tx_h, "power_dbm": tx_power, "freq_hz": f_hz},
            "grid_size": body.grid_size,
            "bbox": body.bbox,
            "feasible_coverage_pct": feasible_pct,
            "signal_min_dbm": round(min(signals), 2),
            "signal_max_dbm": round(max(signals), 2),
            "signal_mean_dbm": round(sum(signals) / len(signals), 2),
            "model_source": (
                "sagemaker" if _cp.SAGEMAKER_ENDPOINT
                else ("local-model" if model else "physics-fallback")
            ),
            "model_version": (
                f"sagemaker:{_cp.SAGEMAKER_ENDPOINT}" if _cp.SAGEMAKER_ENDPOINT
                else (model.version if model else "physics-v1")
            ),
            "points": [
                {"lat": p.lat, "lon": p.lon, "signal_dbm": p.signal_dbm, "feasible": p.feasible}
                for p in grid
            ],
        }
        if body.report_format == "pdf":
            pdf_buffer = render_coverage_predict_pdf(body.model_dump(mode="json"), payload)
            return StreamingResponse(
                pdf_buffer,
                media_type="application/pdf",
                headers={"Content-Disposition": "attachment; filename=coverage_predict_grid.pdf"},
            )
        return payload

    # ── Point mode ─────────────────────────────────────────────────
    if body.rx_lat is None or body.rx_lon is None:
        raise HTTPException(
            status_code=422,
            detail="Point mode requires rx_lat and rx_lon (or pass bbox for grid mode)",
        )
    d_km = LinkEngine.haversine_km(tx_lat, tx_lon, body.rx_lat, body.rx_lon)
    profile = await platform.elevation.get_profile(tx_lat, tx_lon, body.rx_lat, body.rx_lon)
    tx_ground = profile[0] if profile else 0.0
    rx_ground = profile[-1] if profile else 0.0

    result = _cp.predict_signal(
        d_km=d_km,
        f_hz=f_hz,
        tx_h_m=tx_h,
        rx_h_m=body.rx_height_m,
        tx_power_dbm=tx_power,
        tx_gain_dbi=body.tx_gain_dbi,
        rx_gain_dbi=body.rx_gain_dbi,
        terrain_profile=profile,
        tx_ground_elev_m=tx_ground,
        rx_ground_elev_m=rx_ground,
        feasibility_threshold_dbm=body.feasibility_threshold_dbm,
        rx_lat=body.rx_lat,
        rx_lon=body.rx_lon,
        tx_lat=tx_lat,
        tx_lon=tx_lon,
        model=body.model,
    )

    response: Dict[str, Any] = {
        "mode": "point",
        "engine_used": effective_engine,
        "tx": {"lat": tx_lat, "lon": tx_lon, "height_m": tx_h, "power_dbm": tx_power, "freq_hz": f_hz},
        "rx": {"lat": body.rx_lat, "lon": body.rx_lon, "height_m": body.rx_height_m},
        "distance_km": round(d_km, 3),
        "signal_dbm": result.signal_dbm,
        "feasible": result.feasible,
        "confidence": result.confidence,
        "model_source": result.source,
        "model_version": result.model_version,
        "features": result.features,
        "clutter_class": result.clutter_class,
        "clutter_label": result.clutter_label,
    }
    if body.explain:
        response["explanation"] = _cp.explain(response)
    if body.report_format == "pdf":
        pdf_buffer = render_coverage_predict_pdf(body.model_dump(mode="json"), response)
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=coverage_predict_point.pdf"},
        )
    return response


# ─────────────────────────────────────────────────────────────────────
# Per-tier caps for the heatmap grid. Caps the *total* number of cells
# (grid_size**2) so a 50 m grid over a 5 km bbox = 100x100 = 10k cells
# fits within Business / Enterprise but is denied for Pro.
# ─────────────────────────────────────────────────────────────────────
_HEATMAP_MAX_CELLS = {
    Tier.PRO:        2_500,    # 50 x 50
    Tier.BUSINESS:   10_000,   # 100 x 100
    Tier.ENTERPRISE: 40_000,   # 200 x 200
    Tier.ULTRA:     160_000,   # 400 x 400
}


def _resolve_grid_size(body: "CoveragePredictRequest", tier: Tier) -> int:
    """Pick a grid_size honouring ``cell_size_m`` and per-tier safety caps.

    ``cell_size_m`` (when set) overrides ``body.grid_size`` so the UI can
    request a real-world resolution (50 m, 100 m, …) instead of a cell
    count.
    """
    import coverage_predict as _cp
    if body.bbox is None:
        return body.grid_size
    if body.cell_size_m is not None:
        try:
            grid_size = _cp.grid_size_for_cell_size(tuple(body.bbox), body.cell_size_m)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        grid_size = body.grid_size

    cap = _HEATMAP_MAX_CELLS.get(tier, 2_500)
    max_side = int(cap ** 0.5)
    if grid_size > max_side:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Heatmap grid {grid_size}x{grid_size} ({grid_size * grid_size} cells) "
                f"exceeds {tier.value} tier cap of {cap} cells "
                f"({max_side}x{max_side}). Reduce cell_size_m, shrink bbox, "
                "or upgrade your plan."
            ),
        )
    return grid_size


@app.post("/coverage/predict/stream")
async def coverage_predict_stream(
    body: CoveragePredictRequest,
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Server-Sent-Events real-time AI heatmap.

    Streams ``GridPoint`` rows as soon as each cell is predicted so a
    front-end map can paint the heatmap progressively (50x50 m cells over
    a city block render in <1 s of perceived latency).

    Use ``cell_size_m`` (e.g. 50) to request a physical resolution rather
    than a fixed cell count. Caps apply per tier (Pro: 2.5k cells,
    Business: 10k, Enterprise: 40k).

    Each event is JSON: ``{"lat","lon","signal_dbm","feasible","class"}``,
    plus a final ``event: done`` carrying summary stats.
    """
    if body.bbox is None:
        raise HTTPException(
            status_code=422,
            detail="Streaming heatmap requires bbox (use POST /coverage/predict for point mode).",
        )

    import coverage_predict as _cp
    import coverage_export as _cx

    # Resolve transmitter (mirrors coverage_predict)
    if body.tower_id:
        tower = platform.get_tower(body.tower_id)
        if not tower:
            raise HTTPException(status_code=404, detail=f"Tower {body.tower_id} not found")
        tx_lat, tx_lon = tower.lat, tower.lon
        tx_h, tx_power = tower.height_m, tower.power_dbm
        f_hz = tower.primary_freq_hz()
    else:
        if (body.tx_lat is None or body.tx_lon is None
                or body.tx_height_m is None or body.band is None):
            raise HTTPException(
                status_code=422,
                detail="Provide either tower_id or tx_lat/tx_lon/tx_height_m/band",
            )
        tx_lat = body.tx_lat
        tx_lon = body.tx_lon
        tx_h = body.tx_height_m
        tx_power = body.tx_power_dbm
        f_hz = body.band.to_hz()

    grid_size = _resolve_grid_size(body, key_data["tier"])

    async def _events():
        count = 0
        feasible = 0
        s_min = float("inf")
        s_max = float("-inf")
        s_sum = 0.0
        # Initial header event lets the client size the canvas before
        # the first cell arrives.
        header = {
            "event": "start",
            "grid_size": grid_size,
            "total_cells": grid_size * grid_size,
            "bbox": body.bbox,
            "tx": {"lat": tx_lat, "lon": tx_lon, "height_m": tx_h,
                   "power_dbm": tx_power, "freq_hz": f_hz},
        }
        yield f"event: start\ndata: {json.dumps(header)}\n\n"
        try:
            async for p in _cp.predict_coverage_grid_stream(
                tx_lat=tx_lat, tx_lon=tx_lon, tx_h_m=tx_h, f_hz=f_hz,
                bbox=tuple(body.bbox), grid_size=grid_size,
                rx_h_m=body.rx_height_m, tx_power_dbm=tx_power,
                tx_gain_dbi=body.tx_gain_dbi, rx_gain_dbi=body.rx_gain_dbi,
                elevation_service=platform.elevation,
                feasibility_threshold_dbm=body.feasibility_threshold_dbm,
            ):
                count += 1
                if p.feasible:
                    feasible += 1
                if p.signal_dbm < s_min:
                    s_min = p.signal_dbm
                if p.signal_dbm > s_max:
                    s_max = p.signal_dbm
                s_sum += p.signal_dbm
                label, _color = _cx.classify(p.signal_dbm)
                row = {
                    "lat": round(p.lat, 6),
                    "lon": round(p.lon, 6),
                    "signal_dbm": round(p.signal_dbm, 2),
                    "feasible": p.feasible,
                    "class": label,
                }
                yield f"data: {json.dumps(row)}\n\n"
        except ValueError as e:
            yield f"event: error\ndata: {json.dumps({'detail': str(e)})}\n\n"
            return
        summary = {
            "event": "done",
            "count": count,
            "feasible_pct": round(100.0 * feasible / count, 1) if count else 0.0,
            "signal_min_dbm": round(s_min, 2) if count else None,
            "signal_max_dbm": round(s_max, 2) if count else None,
            "signal_mean_dbm": round(s_sum / count, 2) if count else None,
        }
        yield f"event: done\ndata: {json.dumps(summary)}\n\n"

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Geo-format exports (KML / Shapefile / GeoJSON) for QGIS / AutoCAD
# ─────────────────────────────────────────────────────────────────────

class CoverageExportRequest(CoveragePredictRequest):
    """Same body as ``CoveragePredictRequest`` (bbox required)."""
    pass


# ─────────────────────────────────────────────────────────────────────
# /coverage/observations – ingest real RSSI measurements for retraining
# ─────────────────────────────────────────────────────────────────────

class CoverageObservationInput(BaseModel):
    """One ground-truth RSSI measurement.

    All fields are required so the row is self-contained for offline
    re-training without needing to look up tower metadata. Submit
    ``tower_id`` when the measurement is associated with a known tower
    (used for auditing).
    """
    tower_id: Optional[str] = None
    tx_lat: float = Field(..., ge=-90, le=90)
    tx_lon: float = Field(..., ge=-180, le=180)
    tx_height_m: float = Field(..., gt=0, le=500)
    tx_power_dbm: float = Field(..., ge=0, le=80)
    tx_gain_dbi: float = 17.0
    rx_lat: float = Field(..., ge=-90, le=90)
    rx_lon: float = Field(..., ge=-180, le=180)
    rx_height_m: float = Field(default=1.5, ge=0, le=500)
    rx_gain_dbi: float = 0.0
    # Aggregate rx-side passive loss (jumper + connector + lightning
    # protector). Kept default 0.0 so legacy and synthetic submissions
    # remain exactly correct; drive-test sources (`source=drivetest_*`)
    # are required to populate it explicitly — see the validator on the
    # POST /coverage/observations endpoints.
    cable_loss_db: float = Field(default=0.0, ge=0, le=20)
    freq_hz: float = Field(..., gt=1e6, le=100e9)
    observed_dbm: float = Field(..., ge=-150, le=30)
    source: str = Field(default="api", max_length=32)
    ts: Optional[float] = None  # epoch seconds; default to ingest time

    # Drive-test rows must NOT silently inherit the synthetic-friendly
    # defaults (17 dBi tx gain, 0 dBi rx gain, 0 dB cable loss). Those
    # defaults exist purely for backward compatibility with `source=api`
    # and synthetic ingest paths; if a real-world calibrated label
    # arrives without them populated, the model would learn against a
    # systematically biased basic-loss target. We therefore require
    # `drivetest_*` rows to set every rx-side calibration field
    # explicitly. `tx_power_dbm` is already mandatory via `Field(...)`.
    _DRIVETEST_REQUIRED_FIELDS = (
        "tx_gain_dbi",
        "rx_gain_dbi",
        "cable_loss_db",
        "rx_height_m",
    )

    @model_validator(mode="after")
    def _require_explicit_calibration_for_drivetest(self) -> "CoverageObservationInput":
        if not self.source.startswith("drivetest_"):
            return self
        missing = [
            f for f in self._DRIVETEST_REQUIRED_FIELDS
            if f not in self.model_fields_set
        ]
        if missing:
            raise ValueError(
                "source=drivetest_* requires explicit values for: "
                + ", ".join(missing)
                + " (defaults are calibrated for synthetic data only)"
            )
        return self


class CoverageObservationsBatch(BaseModel):
    observations: List[CoverageObservationInput] = Field(..., min_length=1, max_length=10_000)


def _record_coverage_accuracy_metrics(obs: "CoverageObservationInput") -> None:
    """Compute the model prediction for an observation and feed three
    Prometheus histograms (predicted_dbm, measured_dbm, residual_db) so a
    Grafana panel can overlay predicted vs measured RSSI quantiles.

    Failures here MUST NOT propagate — observation ingestion is the
    primary contract and metrics are best-effort instrumentation.
    """
    try:
        from coverage_predict import predict_signal, haversine_km
        d_km = haversine_km(obs.tx_lat, obs.tx_lon, obs.rx_lat, obs.rx_lon)
        pred = predict_signal(
            d_km=d_km,
            f_hz=obs.freq_hz,
            tx_h_m=obs.tx_height_m,
            rx_h_m=obs.rx_height_m,
            tx_power_dbm=obs.tx_power_dbm,
            tx_gain_dbi=obs.tx_gain_dbi,
            rx_gain_dbi=obs.rx_gain_dbi,
        )
        src = pred.source  # "sagemaker" | "local-model" | "physics-fallback"
        COVERAGE_PREDICTED_DBM.labels(source=src).observe(pred.signal_dbm)
        COVERAGE_MEASURED_DBM.labels(source=src).observe(obs.observed_dbm)
        COVERAGE_RESIDUAL_DB.labels(source=src).observe(
            pred.signal_dbm - obs.observed_dbm
        )
        COVERAGE_OBSERVATIONS_TOTAL.labels(source=src).inc()
    except Exception:
        logger.exception("coverage accuracy metrics failed (non-fatal)")


@app.post("/coverage/observations")
async def submit_coverage_observation(
    request: Request,
    body: CoverageObservationInput,
    key_data: Dict = Depends(verify_api_key),
):
    """Submit a single ground-truth RSSI measurement.

    Stored in ``link_observations`` and incorporated into the model on the
    next ``python -m coverage_predict train --with-observations`` run.
    """
    from observation_store import ObservationStore
    store = ObservationStore()
    submitter = _caller_owner(request, key_data)
    obs_id = store.insert_observation({**body.model_dump(), "submitted_by": submitter})
    _record_coverage_accuracy_metrics(body)
    return {"id": obs_id, "status": "stored"}


@app.post("/coverage/observations/batch")
async def submit_coverage_observations_batch(
    request: Request,
    body: CoverageObservationsBatch,
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Bulk-ingest measurements (drive-test CSV uploads, etc.)."""
    from observation_store import ObservationStore
    store = ObservationStore()
    submitter = _caller_owner(request, key_data)
    rows = [{**o.model_dump(), "submitted_by": submitter} for o in body.observations]
    n = store.insert_observations_many(rows)
    for o in body.observations:
        _record_coverage_accuracy_metrics(o)
    return {"ingested": n, "status": "stored"}


# ---------------------------------------------------------------------------
# Drive-test CSV importer (TEMS / G-NetTrack / QualiPoc / Anatel)
# ---------------------------------------------------------------------------
#
# The various commercial drive-test tools each emit CSVs with their own
# column conventions. Rather than asking the field engineer to remap
# columns by hand, this importer auto-detects the most common aliases
# and normalises every row into the shared `link_observations` schema
# with `source='drive_test'`.

_DT_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "lat": (
        "lat", "latitude", "rx_lat", "Latitude", "LAT",
        "Lat. [deg]", "GPS Latitude",
    ),
    "lon": (
        "lon", "lng", "long", "longitude", "rx_lon", "Longitude", "LON",
        "Lon. [deg]", "GPS Longitude",
    ),
    "signal_dbm": (
        "signal_dbm", "observed_dbm", "rssi", "rsrp", "rscp", "rxlev",
        "RSRP", "RSCP", "RxLev", "Signal", "Signal Level",
        "Best Signal Level [dBm]", "DL_RSRP",
    ),
    "band_mhz": (
        "band", "band_mhz", "frequency_mhz", "freq_mhz", "Band",
        "Frequency [MHz]", "DL Frequency [MHz]",
    ),
    "freq_hz": ("freq_hz", "frequency_hz"),
    "ts": ("timestamp", "ts", "time", "Time", "Date", "DateTime"),
    "rx_height_m": ("rx_height_m", "antenna_height_m", "AntHeight"),
}

# Commercial cellular bands (centre frequency in Hz) — used when a
# row has only `band_mhz` and we need a freq_hz to feed the model.
_BAND_MHZ_TO_HZ: Dict[int, float] = {
    700: 700e6, 800: 800e6, 850: 850e6, 900: 900e6,
    1700: 1700e6, 1800: 1800e6, 1900: 1900e6, 2100: 2100e6,
    2300: 2300e6, 2500: 2500e6, 2600: 2600e6,
    3500: 3500e6, 3700: 3700e6, 5800: 5800e6,
}


def _resolve_dt_column(fieldnames: Iterable[str], target: str) -> Optional[str]:
    """Return the actual CSV header that maps to ``target``, or None."""
    aliases = _DT_COLUMN_ALIASES.get(target, ())
    fields = list(fieldnames or [])
    lower_to_orig = {f.strip().lower(): f for f in fields}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lower_to_orig:
            return lower_to_orig[key]
    return None


def _parse_dt_timestamp(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)  # epoch seconds
    except (TypeError, ValueError):
        pass
    s = str(raw).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _band_mhz_to_freq_hz(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # Heuristic: > 1e6 → already Hz. > 1e3 → already MHz expressed as Hz/1e3?
    # We treat the raw value as MHz unless > 5e4 (>50 GHz is non-cellular).
    if v > 5e4:  # already in Hz
        return v
    if v <= 0:
        return None
    # Snap to closest known commercial band when within 50 MHz
    bands = sorted(_BAND_MHZ_TO_HZ.keys())
    closest = min(bands, key=lambda b: abs(b - v))
    if abs(closest - v) <= 50:
        return _BAND_MHZ_TO_HZ[closest]
    return v * 1e6


@app.post("/coverage/observations/drivetest")
async def import_drive_test_csv(
    request: Request,
    csv_file: UploadFile = File(..., description="Drive-test export from TEMS / G-NetTrack / QualiPoc / Anatel"),
    tower_id: Optional[str] = Form(None, description="If set, TX context is read from this tower."),
    tx_lat: Optional[float] = Form(None),
    tx_lon: Optional[float] = Form(None),
    tx_height_m: Optional[float] = Form(None),
    tx_power_dbm: Optional[float] = Form(None),
    tx_gain_dbi: float = Form(17.0),
    rx_height_m: float = Form(1.5),
    default_band_mhz: Optional[int] = Form(None, description="Used if the CSV has no band/freq column."),
    device: str = Form("drive_test", description="Free-form label, e.g. 'tems', 'gnettrack', 'qualipoc'."),
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Bulk-ingest a drive-test CSV (TEMS / G-NetTrack / QualiPoc / Anatel).

    Column aliases are auto-detected. The minimum required columns are
    ``lat``/``lon`` and one of ``signal_dbm``/``rsrp``/``rscp``/``rxlev``.
    Frequency is taken from (in priority order): per-row ``freq_hz``,
    per-row ``band_mhz`` snapped to the nearest commercial band, the
    ``default_band_mhz`` form field, or the tower's primary frequency
    when ``tower_id`` is provided.

    TX context resolution:
      * ``tower_id`` → loads tower; uses its lat/lon/height/power/freq.
      * Otherwise: ``tx_lat``, ``tx_lon``, ``tx_height_m``, ``tx_power_dbm``
        form fields are required.

    Rows are persisted to ``link_observations`` with ``source='drive_test'``
    so the next ``retrain-coverage-model`` pass picks them up.
    """
    # ---- TX context ------------------------------------------------------
    tower_obj = None
    tower_freq_hz: Optional[float] = None
    if tower_id:
        tower_obj = platform.get_tower(tower_id)
        if not tower_obj:
            raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
        tx_lat = tower_obj.lat
        tx_lon = tower_obj.lon
        tx_height_m = tower_obj.height_m
        tx_power_dbm = tower_obj.power_dbm
        tower_freq_hz = float(tower_obj.primary_freq_hz())

    missing = [
        n for n, v in (
            ("tx_lat", tx_lat), ("tx_lon", tx_lon),
            ("tx_height_m", tx_height_m), ("tx_power_dbm", tx_power_dbm),
        ) if v is None
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Provide either tower_id or all of: tx_lat, tx_lon, tx_height_m, tx_power_dbm. Missing: {missing}",
        )

    # ---- Read CSV --------------------------------------------------------
    contents = await csv_file.read()
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = contents.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=400, detail="CSV must be UTF-8 or latin-1 encoded")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV has no header row")

    col_lat = _resolve_dt_column(reader.fieldnames, "lat")
    col_lon = _resolve_dt_column(reader.fieldnames, "lon")
    col_sig = _resolve_dt_column(reader.fieldnames, "signal_dbm")
    col_band = _resolve_dt_column(reader.fieldnames, "band_mhz")
    col_freq_hz = _resolve_dt_column(reader.fieldnames, "freq_hz")
    col_ts = _resolve_dt_column(reader.fieldnames, "ts")
    col_rxh = _resolve_dt_column(reader.fieldnames, "rx_height_m")

    if not (col_lat and col_lon and col_sig):
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV must have lat, lon and a signal column. "
                f"Found: {reader.fieldnames}. Recognised aliases: "
                f"lat={_DT_COLUMN_ALIASES['lat']}, "
                f"lon={_DT_COLUMN_ALIASES['lon']}, "
                f"signal={_DT_COLUMN_ALIASES['signal_dbm']}"
            ),
        )

    default_freq_hz: Optional[float] = None
    if default_band_mhz is not None:
        default_freq_hz = _band_mhz_to_freq_hz(default_band_mhz)
    if default_freq_hz is None:
        default_freq_hz = tower_freq_hz

    # ---- Parse rows ------------------------------------------------------
    submitter = _caller_owner(request, key_data)
    tier_limit = TIER_LIMITS[key_data["tier"]]["max_batch_rows"]

    rows: List[Dict[str, Any]] = []
    skipped = 0
    for row_num, row in enumerate(reader, start=2):
        if len(rows) >= tier_limit:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Drive-test CSV exceeds the {key_data['tier'].value} tier limit "
                    f"of {tier_limit} rows. Split the file or upgrade your plan."
                ),
            )
        try:
            lat = float(row[col_lat])
            lon = float(row[col_lon])
            sig = float(row[col_sig])
        except (TypeError, ValueError, KeyError):
            skipped += 1
            continue

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            skipped += 1
            continue
        if not (-150.0 <= sig <= 30.0):
            skipped += 1
            continue

        # Frequency resolution priority: row freq_hz > row band_mhz > default
        f_hz: Optional[float] = None
        if col_freq_hz:
            try:
                f_hz = float(row[col_freq_hz])
            except (TypeError, ValueError):
                f_hz = None
        if f_hz is None and col_band:
            f_hz = _band_mhz_to_freq_hz(row.get(col_band))
        if f_hz is None:
            f_hz = default_freq_hz
        if f_hz is None or f_hz <= 1e6:
            skipped += 1
            continue

        ts_val: Optional[float] = None
        if col_ts:
            ts_val = _parse_dt_timestamp(row.get(col_ts))

        rx_h = rx_height_m
        if col_rxh:
            try:
                rx_h = float(row[col_rxh])
            except (TypeError, ValueError):
                pass

        rows.append({
            "ts": ts_val or time.time(),
            "tower_id": tower_id,
            "tx_lat": tx_lat, "tx_lon": tx_lon,
            "tx_height_m": tx_height_m, "tx_power_dbm": tx_power_dbm,
            "tx_gain_dbi": tx_gain_dbi,
            "rx_lat": lat, "rx_lon": lon,
            "rx_height_m": rx_h, "rx_gain_dbi": 0.0,
            "freq_hz": f_hz,
            "observed_dbm": sig,
            "source": "drive_test",
            "submitted_by": submitter,
        })

    if not rows:
        raise HTTPException(
            status_code=400,
            detail=f"No valid rows parsed (skipped {skipped}). Check column mappings.",
        )

    from observation_store import ObservationStore
    store = ObservationStore()
    n = store.insert_observations_many(rows)

    # Best-effort prediction-vs-measured metrics on a sampled subset to
    # avoid CPU spikes on multi-thousand-row uploads.
    sample_step = max(1, n // 200)
    for r in rows[::sample_step]:
        try:
            _record_coverage_accuracy_metrics(CoverageObservationInput(**{
                k: v for k, v in r.items() if k in CoverageObservationInput.model_fields
            }))
        except Exception:
            pass

    return {
        "status": "stored",
        "ingested": n,
        "skipped": skipped,
        "device": device,
        "tower_id": tower_id,
        "columns_detected": {
            "lat": col_lat, "lon": col_lon, "signal": col_sig,
            "band": col_band, "freq_hz": col_freq_hz,
            "timestamp": col_ts, "rx_height": col_rxh,
        },
    }


@app.get("/coverage/observations/stats")
async def coverage_observations_stats(_key: Dict = Depends(verify_api_key)):
    """Return current row counts for the training stores (for ops dashboards)."""
    try:
        from observation_store import ObservationStore
        return ObservationStore().counts()
    except Exception as e:
        logger.exception("coverage_observations_stats failed")
        raise HTTPException(
            status_code=500,
            detail=f"observation_store error: {type(e).__name__}: {e}",
        )


@app.get("/coverage/model/info")
async def coverage_model_info(
    refresh: bool = False,
    _key: Dict = Depends(verify_api_key),
):
    """Return metadata about the currently loaded coverage model.

    ``?refresh=true`` re-reads ``coverage_model.npz`` from disk (or S3 via
    ``COVERAGE_MODEL_S3_URI``) so a freshly retrained artifact can be
    picked up by a long-lived task without a full restart.
    """
    try:
        import coverage_predict
        sm_endpoint = coverage_predict.SAGEMAKER_ENDPOINT
        model = coverage_predict.get_model(refresh=refresh)
        band_model = coverage_predict.get_band_model(refresh=refresh)
    except Exception as e:
        logger.exception("coverage_model_info failed")
        raise HTTPException(
            status_code=500,
            detail=f"coverage_predict error: {type(e).__name__}: {e}",
        )

    payload: Dict[str, Any] = {
        "sagemaker_endpoint": sm_endpoint or None,
        "local_model": None,
        "model_path": coverage_predict.MODEL_PATH,
        "model_s3_uri": coverage_predict.MODEL_S3_URI or None,
        "band_aware": band_model.info() if band_model is not None else None,
        "band_model_dir": coverage_predict.BAND_MODEL_DIR or None,
    }
    if model is not None:
        payload["local_model"] = {
            "version": model.version,
            "rmse_db": round(model.rmse_db, 4),
            "n_train": model.n_train,
            "trained_at": model.trained_at,
            "feature_count": int(len(model.feature_mean)),
            "cv_rmse_db": round(model.cv_rmse_db, 4),
            "cv_rmse_std_db": round(model.cv_rmse_std_db, 4),
            "cv_folds": int(model.cv_folds),
            "rmse_by_morphology": dict(model.rmse_by_morphology or {}),
            "rmse_by_band": dict(model.rmse_by_band or {}),
        }
        try:
            COVERAGE_MODEL_RMSE_DB.set(model.rmse_db)
            COVERAGE_MODEL_N_TRAIN.set(model.n_train)
            COVERAGE_MODEL_TRAINED_AT.set(model.trained_at)
            COVERAGE_MODEL_CV_RMSE_DB.set(model.cv_rmse_db)
            COVERAGE_MODEL_CV_RMSE_STD_DB.set(model.cv_rmse_std_db)
            COVERAGE_MODEL_CV_FOLDS.set(model.cv_folds)
            for morph, val in (model.rmse_by_morphology or {}).items():
                COVERAGE_MODEL_RMSE_BY_MORPHOLOGY_DB.labels(morphology=morph).set(val)
            for band, val in (model.rmse_by_band or {}).items():
                COVERAGE_MODEL_RMSE_BY_BAND_DB.labels(band=band).set(val)
        except Exception:
            logger.debug("coverage_model gauge update failed", exc_info=True)
    return payload


@app.post("/coverage/predict/export")
async def coverage_predict_export(
    body: CoverageExportRequest,
    fmt: str = "kml",
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Export a coverage grid as KML / Shapefile / GeoJSON.

    QGIS opens KML and Shapefile natively; AutoCAD Map 3D imports
    Shapefiles. ``fmt`` is one of ``kml`` (default), ``shp``, ``geojson``.

    Pro / Business / Enterprise only. Same per-tier cell caps as the
    real-time heatmap.
    """
    if body.bbox is None:
        raise HTTPException(status_code=422, detail="Export requires bbox.")

    import coverage_predict as _cp
    import coverage_export as _cx

    fmt_lower = fmt.lower()
    if fmt_lower not in _cx.FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported format {fmt!r}; use one of: {', '.join(_cx.FORMATS)}",
        )

    # Resolve transmitter (mirrors coverage_predict)
    if body.tower_id:
        tower = platform.get_tower(body.tower_id)
        if not tower:
            raise HTTPException(status_code=404, detail=f"Tower {body.tower_id} not found")
        tx_lat, tx_lon = tower.lat, tower.lon
        tx_h, tx_power = tower.height_m, tower.power_dbm
        f_hz = tower.primary_freq_hz()
    else:
        if (body.tx_lat is None or body.tx_lon is None
                or body.tx_height_m is None or body.band is None):
            raise HTTPException(
                status_code=422,
                detail="Provide either tower_id or tx_lat/tx_lon/tx_height_m/band",
            )
        tx_lat = body.tx_lat
        tx_lon = body.tx_lon
        tx_h = body.tx_height_m
        tx_power = body.tx_power_dbm
        f_hz = body.band.to_hz()

    grid_size = _resolve_grid_size(body, key_data["tier"])

    try:
        grid = await _cp.predict_coverage_grid(
            tx_lat=tx_lat, tx_lon=tx_lon, tx_h_m=tx_h, f_hz=f_hz,
            bbox=tuple(body.bbox), grid_size=grid_size,
            rx_h_m=body.rx_height_m, tx_power_dbm=tx_power,
            tx_gain_dbi=body.tx_gain_dbi, rx_gain_dbi=body.rx_gain_dbi,
            elevation_service=platform.elevation,
            feasibility_threshold_dbm=body.feasibility_threshold_dbm,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    meta = {
        "tx": {"lat": tx_lat, "lon": tx_lon, "height_m": tx_h,
               "power_dbm": tx_power, "freq_hz": f_hz},
        "grid_size": grid_size,
        "bbox": body.bbox,
        "feasibility_threshold_dbm": body.feasibility_threshold_dbm,
        "generated_at": time.time(),
    }
    name = f"coverage_{int(time.time())}"
    try:
        payload, content_type, filename = _cx.export(grid, fmt_lower, name=name, meta=meta)
    except RuntimeError as e:
        # Missing optional dep (simplekml / pyshp). 503 to differentiate
        # from a request error — the caller can retry once we redeploy.
        raise HTTPException(status_code=503, detail=str(e))

    return Response(
        content=payload,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────
# /coverage/interference — co-channel + adjacent-channel aggregation
# ─────────────────────────────────────────────────────────────────────
# Sums received interference power at a victim receiver from every
# tower in the database within ``search_radius_km`` whose primary band
# falls within the spectral mask of the victim. Pluggable propagation
# engine (FSPL today; ITM / P.1812 / Sionna RT in T17.5+).
#
# Pure additive feature: it neither reads nor mutates the heatmap
# pipeline. The maths lives in ``interference_engine.py``; this
# endpoint only does the DB lookup, engine selection, response
# packaging and audit logging.

class _InterferenceVictim(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    freq_mhz: float = Field(..., gt=0, le=300_000)
    bw_mhz: float = Field(default=20.0, gt=0, le=2000)
    rx_height_m: float = Field(default=10.0, ge=0, le=500)
    rx_gain_dbi: float = Field(default=12.0, ge=-10, le=40)
    noise_figure_db: float = Field(default=5.0, ge=0, le=20)
    # If supplied, the response includes SINR. Otherwise only I/N.
    victim_signal_dbm: Optional[float] = Field(default=None, ge=-160, le=30)
    # T20 — victim PLMN tag (purely informational; echoed back in the
    # response for downstream MOCN reporting). Not used as a filter.
    plmn: Optional[str] = Field(default=None, max_length=6,
        description="Victim home PLMN, e.g. '72411' (Vivo)")
    # T20 — receive-side MIMO array size. For Sionna RT this configures
    # the RX PlanarArray; for FSPL/P.1812 it folds into a fixed diversity
    # offset (3 dB per doubling, capped at 9 dB ≡ 8x8).
    rx_mimo: int = Field(default=1, ge=1, le=64,
        description="Receive-side antenna count (MIMO array size); 1=SISO")


class InterferenceRequest(BaseModel):
    """Body for ``POST /coverage/interference``."""

    victim: _InterferenceVictim
    search_radius_km: float = Field(default=30.0, gt=0, le=200)
    top_n: int = Field(default=10, ge=1, le=50)
    include_aci: bool = Field(default=True,
        description="Include adjacent-channel aggressors with mask attenuation. "
                    "Set false for co-channel-only studies.")
    engine: str = Field(default="auto",
        description="Path-loss engine: auto | fspl | itu-p1812 | itmlogic | sionna-rt")
    aggressor_tx_gain_dbi: float = Field(default=17.0, ge=-10, le=40,
        description="Assumed Tx antenna gain when the tower record has none")
    aci_floor_db: Optional[float] = Field(default=None, ge=10, le=120,
        description="Override the far-out ACI mask floor (default 60 dB)")
    max_aggressors: int = Field(default=200, ge=10, le=2000,
        description="Hard cap on candidate towers fetched from DB before "
                    "in-radius filtering (latency vs completeness)")
    # T20 — MOCN attribution filter. Glob (fnmatch) applied to each
    # aggressor's ``plmn`` column before the path-loss math runs:
    #   * "72411" → Vivo only
    #   * "724*"  → all Brazil PLMNs
    #   * None    → no filter (default; backward compatible)
    # Aggressors with a NULL ``plmn`` only match when the filter is None.
    aggressor_plmn: Optional[str] = Field(default=None, max_length=10,
        description="PLMN glob filter for aggressors (e.g. '72411' or '724*')")
    report_format: Literal["json", "pdf"] = Field(
        default="json",
        description="Response format. 'pdf' returns an engineering report rendered with WeasyPrint.",
    )


class _AggressorOut(BaseModel):
    aggressor_id: str
    operator: str
    distance_km: float
    aggressor_freq_mhz: float
    aggressor_bw_mhz: float
    delta_f_mhz: float
    eirp_dbm: float
    path_loss_db: float
    aci_db: float
    rx_power_dbm: float
    # T20 fields. ``plmn`` is None for legacy rows imported before
    # T20 (no MCC/MNC mapping was persisted). ``mimo_gain_db`` is
    # the diversity offset applied at this aggressor (FSPL/P.1812
    # only; Sionna RT bakes MIMO into ``path_loss_db`` and reports 0.0).
    plmn: Optional[str] = None
    mimo_gain_db: float = 0.0


class InterferenceResponse(BaseModel):
    victim: dict
    engine: str
    n_candidates: int                # towers fetched from DB
    n_in_radius: int                 # within search_radius_km
    n_contributing: int              # finite Rx power after ACI mask
    co_channel_count: int
    adjacent_channel_count: int
    aggregate_i_dbm: Optional[float]
    noise_dbm: float
    i_over_n_db: Optional[float]
    sinr_db: Optional[float]
    top_n_aggressors: List[_AggressorOut]
    # T20 — MOCN/MIMO diagnostics. ``n_filtered_by_plmn`` counts
    # aggressors skipped because their ``plmn`` failed the request
    # filter (NULL plmn vs explicit pattern, or pattern mismatch).
    # The two aggregate maps sum interference power in the linear
    # domain (mW) per operator string and per PLMN, then convert
    # back to dBm for the response. Both default to empty dicts so
    # pre-T20 clients that ignore them are unaffected.
    n_filtered_by_plmn: int = 0
    aggregate_by_operator_dbm: Dict[str, float] = Field(default_factory=dict)
    aggregate_by_plmn_dbm: Dict[str, float] = Field(default_factory=dict)


_INTERFERENCE_SUPPORTED_ENGINES = {"auto", "fspl", "itu-p1812", "itmlogic", "sionna-rt"}


def _interference_select_engine(requested: str) -> str:
    """Resolve ``engine='auto'`` to a concrete engine name.

    For v1 we only ship FSPL (no terrain fetch — interference studies
    span 200+ aggressors and the SRTM round-trip would 30× the latency).
    Other engines return 501 until the synchronous path is wired in
    T17.5 (ITM / P.1812 with cached terrain) and T18 (Sionna RT async
    job pattern).
    """
    if requested not in _INTERFERENCE_SUPPORTED_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown engine: {requested!r}; "
                   f"choose from {sorted(_INTERFERENCE_SUPPORTED_ENGINES)}",
        )
    if requested in ("auto", "fspl"):
        return "fspl"
    if requested == "sionna-rt":
        return "sionna-rt"
    raise HTTPException(
        status_code=501,
        detail=f"engine {requested!r} not yet supported in /coverage/interference; "
               "use engine='fspl' (or 'auto') or 'sionna-rt' "
               "(GPU + scene required). ITM / P.1812 path is on the T17.5+ roadmap.",
    )


@app.post("/coverage/interference", response_model=InterferenceResponse)
async def coverage_interference(
    body: InterferenceRequest,
    request: Request,
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
) -> InterferenceResponse:
    """Aggregate co-channel + adjacent-channel interference at a victim Rx.

    Returns the summed interference power, I/N, and (when
    ``victim.victim_signal_dbm`` is provided) SINR, plus a top-N list
    of the strongest aggressors so the operator can sequence their
    re-tilt / re-frequency mitigation work.

    Pro / Business / Enterprise / Ultra. Free / Starter excluded —
    the DB radius scan is meaningfully more expensive than a single
    point predict and the answer materially affects operator capex
    decisions.
    """
    from interference_engine import (
        aggregate_interference_dbm,
        aggregate_by_key,
        build_contribution,
        i_over_n_db as _i_over_n,
        mimo_diversity_gain_db,
        plmn_matches,
        sinr_db as _sinr,
        thermal_noise_dbm,
        top_n_contributions,
    )

    engine_name = _interference_select_engine(body.engine)

    victim_f_hz = body.victim.freq_mhz * 1e6
    victim_bw_hz = body.victim.bw_mhz * 1e6

    # 1) Candidate aggressors from the DB. We over-fetch (max_aggressors)
    #    and then filter by haversine — find_nearest_towers returns a
    #    bounded set ordered by approximate distance, which is enough.
    owner = _caller_owner(request, key_data)
    candidates = platform.find_nearest_towers(
        body.victim.lat, body.victim.lon,
        operator=None, limit=body.max_aggressors, owner=owner,
    )

    # 2) Filter by exact distance and build per-pair contributions.
    contributions: List = []
    co_count = 0
    adj_count = 0
    in_radius = 0
    n_engine_failures = 0

    # Engine selection: FSPL is computed inline (cheap closed-form);
    # Sionna RT defers to the dedicated handler in
    # ``rf_engines.interference_engine`` which calls predict_basic_loss
    # per aggressor. Other engines were rejected upstream by
    # ``_interference_select_engine``.
    sionna_rt_handler = None
    if engine_name == "sionna-rt":
        from rf_engines.interference_engine import (
            SionnaRTInterferenceHandler,
        )
        sionna_rt_handler = SionnaRTInterferenceHandler()
        if not sionna_rt_handler.is_available():
            raise HTTPException(
                status_code=503,
                detail="sionna-rt engine not available: set SIONNA_RT_DISABLED=0, "
                       "SIONNA_RT_SCENE_PATH to a directory with scene.xml + "
                       "manifest.json, and ensure mitsuba + sionna_rt imports succeed",
            )

    n_filtered_plmn = 0  # T20
    rx_mimo = int(body.victim.rx_mimo or 1)

    for t in candidates:
        # T20 — MOCN attribution: glob filter against tower's PLMN.
        # Done before the distance check so the response
        # ``n_filtered_by_plmn`` counts every tower the platform
        # returned, regardless of radius.
        tower_plmn = getattr(t, "plmn", None) or None
        if not plmn_matches(tower_plmn, body.aggressor_plmn):
            n_filtered_plmn += 1
            continue

        d_km = LinkEngine.haversine_km(
            body.victim.lat, body.victim.lon, t.lat, t.lon,
        )
        if d_km > body.search_radius_km:
            continue
        if d_km <= 0.001:
            # Co-located receiver/aggressor — degenerate, skip to avoid
            # log10(0) blowup. Operator should re-aim, not re-compute.
            continue
        in_radius += 1

        agg_f_hz = float(t.primary_freq_hz())
        # Most rows in the DB lack an explicit channel bandwidth — assume
        # the same BW as the victim (worst case for ACI mask: equal-BW
        # produces 0 dB co-channel attenuation, which is conservative).
        agg_bw_hz = victim_bw_hz

        # EIRP = Pt + Gt. Tower record carries Pt; assume the operator
        # per-band Gt from the request.
        eirp_dbm = float(t.power_dbm) + body.aggressor_tx_gain_dbi

        # T20 — per-aggressor MIMO state.
        n_tx_ant = int(getattr(t, "n_tx_antennas", 1) or 1)

        if sionna_rt_handler is not None:
            # Sionna RT path-loss per aggressor (deterministic 3D ray
            # tracing). Falls back to skipping the aggressor when the
            # ray solver returns None (e.g. RX outside scene bbox).
            # MIMO geometry is configured directly on the planar arrays;
            # the H-matrix Frobenius norm folds diversity gain into the
            # returned ``basic_loss_db``, so no offset is applied below.
            try:
                est = sionna_rt_handler._engine.predict_basic_loss(
                    f_hz=agg_f_hz,
                    d_km=(0.0, max(d_km, 0.001)),
                    h_m=(0.0, 0.0),
                    htg=float(getattr(t, "height_m", 0.0) or 0.0),
                    hrg=body.victim.rx_height_m,
                    phi_t=t.lat, lam_t=t.lon,
                    phi_r=body.victim.lat, lam_r=body.victim.lon,
                    num_tx_ant=n_tx_ant,
                    num_rx_ant=rx_mimo,
                )
            except Exception:
                logger.exception("sionna-rt predict_basic_loss raised for tower=%s", t.id)
                est = None
            if est is None:
                n_engine_failures += 1
                continue
            pl_db = float(est.basic_loss_db)
            mimo_db = 0.0  # baked into pl_db
        else:
            # FSPL only for v1. Engine plug-points (ITM, etc.) reuse the
            # contribution builder verbatim with a different ``path_loss_db``.
            pl_db = LinkEngine.free_space_path_loss(d_km, agg_f_hz)
            mimo_db = mimo_diversity_gain_db(n_tx_ant, rx_mimo)

        c = build_contribution(
            aggressor_id=t.id,
            distance_km=d_km,
            aggressor_f_hz=agg_f_hz,
            aggressor_bw_hz=agg_bw_hz,
            aggressor_eirp_dbm=eirp_dbm,
            victim_f_hz=victim_f_hz,
            victim_bw_hz=victim_bw_hz,
            rx_gain_dbi=body.victim.rx_gain_dbi,
            path_loss_db=pl_db,
            include_aci=body.include_aci,
            aci_floor_db=body.aci_floor_db,
            plmn=tower_plmn,
            mimo_gain_db=mimo_db,
        )
        if c.aci_db == 0.0:
            co_count += 1
        elif math.isfinite(c.rx_power_dbm):
            adj_count += 1
        contributions.append((t, c))

    # 3) Aggregate + noise + SINR.
    raw_contribs = [c for _, c in contributions]
    i_dbm = aggregate_interference_dbm(raw_contribs)
    n_dbm = thermal_noise_dbm(victim_bw_hz, body.victim.noise_figure_db)
    i_n = _i_over_n(i_dbm, n_dbm)
    sinr = _sinr(body.victim.victim_signal_dbm, i_dbm, n_dbm)

    # 4) Top-N for the response.
    top = top_n_contributions(raw_contribs, n=body.top_n)
    # Re-attach the operator string from the matching tower (the
    # contribution dataclass is engine-agnostic and doesn't carry it).
    by_id = {t.id: t for t, _ in contributions}
    top_out: List[_AggressorOut] = []
    for c in top:
        t = by_id.get(c.aggressor_id)
        delta_mhz = (c.aggressor_f_hz - victim_f_hz) / 1e6
        top_out.append(_AggressorOut(
            aggressor_id=c.aggressor_id,
            operator=(t.operator if t else "unknown"),
            distance_km=round(c.distance_km, 3),
            aggressor_freq_mhz=round(c.aggressor_f_hz / 1e6, 3),
            aggressor_bw_mhz=round(c.aggressor_bw_hz / 1e6, 3),
            delta_f_mhz=round(delta_mhz, 3),
            eirp_dbm=round(c.eirp_dbm, 2),
            path_loss_db=round(c.path_loss_db, 2),
            aci_db=round(c.aci_db, 2),
            rx_power_dbm=round(c.rx_power_dbm, 2),
            plmn=c.plmn,
            mimo_gain_db=round(c.mimo_gain_db, 2),
        ))

    n_contrib = sum(1 for c in raw_contribs if math.isfinite(c.rx_power_dbm))

    # T20 — MOCN aggregations. Operator label comes from the matching
    # tower row ("unknown" when the aggregator couldn't be looked up).
    op_by_id: Dict[str, str] = {t.id: (t.operator or "unknown") for t, _ in contributions}
    agg_by_op = aggregate_by_key(raw_contribs, lambda c: op_by_id.get(c.aggressor_id, "unknown"))
    agg_by_plmn = aggregate_by_key(raw_contribs, lambda c: c.plmn or "unknown")
    agg_by_op = {k: round(v, 2) for k, v in agg_by_op.items()}
    agg_by_plmn = {k: round(v, 2) for k, v in agg_by_plmn.items()}

    response = InterferenceResponse(
        victim={
            "lat": body.victim.lat,
            "lon": body.victim.lon,
            "freq_mhz": body.victim.freq_mhz,
            "bw_mhz": body.victim.bw_mhz,
            "rx_height_m": body.victim.rx_height_m,
            "rx_gain_dbi": body.victim.rx_gain_dbi,
            "noise_figure_db": body.victim.noise_figure_db,
            "plmn": body.victim.plmn,
            "rx_mimo": rx_mimo,
        },
        engine=engine_name,
        n_candidates=len(candidates),
        n_in_radius=in_radius,
        n_contributing=n_contrib,
        co_channel_count=co_count,
        adjacent_channel_count=adj_count,
        aggregate_i_dbm=(round(i_dbm, 2) if i_dbm is not None else None),
        noise_dbm=round(n_dbm, 2),
        i_over_n_db=(round(i_n, 2) if i_n is not None else None),
        sinr_db=(round(sinr, 2) if sinr is not None else None),
        top_n_aggressors=top_out,
        n_filtered_by_plmn=n_filtered_plmn,
        aggregate_by_operator_dbm=agg_by_op,
        aggregate_by_plmn_dbm=agg_by_plmn,
    )
    if body.report_format == "pdf":
        raw_key = request.headers.get("x-api-key", "") or ""
        _enforce_pdf_quota(raw_key, key_data["tier"])
        pdf_buffer = render_interference_pdf(body.model_dump(mode="json"), response.model_dump(mode="json"))
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=interference_report.pdf"},
        )
    return response


# ─────────────────────────────────────────────────────────────────────
# Async interference (T18) — SQS worker path
# ─────────────────────────────────────────────────────────────────────
#
# Same FSPL math as the sync endpoint but designed for very large
# ``search_radius_km`` sweeps where the 200+ candidate per-aggressor
# loop pushes the sync timeout. Submission flow:
#
#   POST /coverage/interference/async
#     ─► resolves candidate towers from the DB *now*
#     ─► persists job in batch_jobs (sentinel tower_id="__interference__")
#     ─► enqueues SQS message {job_id, job_type:"interference", tier}
#     ─► returns {job_id, status:"queued"}
#
#   Worker (sqs_lambda_worker._process_interference_job)
#     ─► loads request + candidates from DB
#     ─► runs interference_engine.compute_interference_fspl()
#     ─► uploads result.json to S3
#     ─► marks completed with result_path=s3://.../result.json
#
#   GET /coverage/interference/jobs/{job_id}
#     ─► polls status; when completed, fetches JSON from S3 and returns inline.

# Sentinel marker: `tower_id` in `batch_jobs` is repurposed as a discriminator
# so the worker dispatches to the right handler; the field is non-null in the
# legacy schema. Real interference jobs never reference a tower in the field.
INTERFERENCE_JOB_SENTINEL = "__interference__"


class _InterferenceJobAccepted(BaseModel):
    job_id: str
    status: str
    n_candidates: int
    poll_url: str
    result_url: str


def _build_candidates_for_request(
    body: "InterferenceRequest", towers: list,
) -> list:
    """Pre-resolve aggressor records so the worker doesn't need DB access.

    Returns a list of plain dicts (CandidateAggressor.to_dict shape) ready
    for JSON serialisation into the job payload. We capture every tower
    the platform returned — the worker re-applies the radius filter so the
    DB query and the math agree if the data races.
    """
    from interference_engine import CandidateAggressor  # local import: keeps cold-start cheap

    victim_bw_hz = body.victim.bw_mhz * 1e6
    out = []
    for t in towers:
        out.append(CandidateAggressor(
            aggressor_id=str(t.id),
            operator=str(t.operator or "unknown"),
            lat=float(t.lat),
            lon=float(t.lon),
            height_m=float(getattr(t, "height_m", 0.0) or 0.0),
            f_hz=float(t.primary_freq_hz()),
            bw_hz=victim_bw_hz,
            eirp_dbm=float(t.power_dbm) + body.aggressor_tx_gain_dbi,
            plmn=getattr(t, "plmn", None) or None,
            n_tx_antennas=int(getattr(t, "n_tx_antennas", 1) or 1),
        ).to_dict())
    return out


@app.post("/coverage/interference/async", response_model=_InterferenceJobAccepted)
async def coverage_interference_async(
    body: InterferenceRequest,
    request: Request,
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
) -> _InterferenceJobAccepted:
    """Submit an interference study to the async worker pool.

    Returns immediately with a ``job_id``; poll
    ``GET /coverage/interference/jobs/{job_id}`` for the result. Use
    this when ``search_radius_km`` is large (50+ km, 200+ aggressors)
    or when the caller doesn't want to hold an HTTP connection open
    for the full compute.

    Engine routing:
      * ``fspl`` / ``auto`` → SQS Lambda worker (CPU, sub-second
        per aggressor).
      * ``sionna-rt`` → AWS Batch GPU job (deterministic ray tracing
        against the deployed scene). Requires
        ``BATCH_JOB_QUEUE_GPU`` + ``BATCH_JOB_DEFINITION_GPU`` env
        vars on the API task; 503 if absent.
    """
    # Resolve engine first; reject unwired engines (ITM, P.1812)
    # the same way the sync endpoint does.
    engine_name = _interference_select_engine(body.engine)
    if engine_name not in ("fspl", "sionna-rt"):
        raise HTTPException(
            status_code=501,
            detail=f"async interference path supports only "
                   f"engine='fspl' / 'auto' / 'sionna-rt'; "
                   f"got resolved engine={engine_name!r}.",
        )

    owner = _caller_owner(request, key_data)
    towers = platform.find_nearest_towers(
        body.victim.lat, body.victim.lon,
        operator=None, limit=body.max_aggressors, owner=owner,
    )
    candidates = _build_candidates_for_request(body, towers)

    job_id = str(uuid.uuid4())
    payload = {
        "job_type": "interference",
        "schema_version": 1,
        "request": body.model_dump(),
        "candidates": candidates,
    }
    api_key = request.headers.get("x-api-key", "")
    job_store.create_job(
        job_id=job_id,
        tower_id=INTERFERENCE_JOB_SENTINEL,
        receivers_json=json.dumps(payload),
        total=len(candidates),
        api_key=api_key,
    )

    tier_value = key_data["tier"].value
    backend = "sqs"
    batch_job_id = ""
    queue_url = ""
    if engine_name == "sionna-rt":
        # GPU AWS Batch job (T19). The container reads the row from
        # the same DB and uploads to the same S3 prefix the SQS
        # Lambda would, so the result-fetch endpoint is engine-agnostic.
        batch_job_id = _submit_gpu_batch_job(job_id, tier_value)
        backend = "batch-gpu"
    else:
        queue_url = _queue_for_tier(tier_value)
        if queue_url:
            _get_sqs().send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "job_type": "interference",
                    "tier": tier_value,
                }),
            )

    await _audit.log(
        api_key,
        "interference.async.create",
        actor_email=key_data.get("email") or key_data.get("owner"),
        tier=tier_value,
        target=f"job:{job_id}",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "n_candidates": len(candidates),
            "search_radius_km": body.search_radius_km,
            "engine": engine_name,
            "backend": backend,
            "batch_job_id": batch_job_id,
            "queue": "priority" if queue_url == SQS_QUEUE_URL_PRIORITY and queue_url else (
                "default" if queue_url else "db-only"
            ),
        },
    )

    return _InterferenceJobAccepted(
        job_id=job_id,
        status="queued",
        n_candidates=len(candidates),
        poll_url=f"/coverage/interference/jobs/{job_id}",
        result_url=f"/coverage/interference/jobs/{job_id}/result",
    )


def _load_interference_result_from_s3(job: Dict) -> Optional[Dict]:
    """Download the result.json blob the worker uploaded for this job."""
    result_path = job.get("result_path") or ""
    if not result_path.startswith("s3://"):
        return None
    # Parse s3://bucket/key
    rest = result_path[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        return None
    try:
        import boto3 as _boto3
        s3 = _boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read())
    except Exception:
        logger.exception("Failed to fetch interference result from %s", result_path)
        return None


@app.get("/coverage/interference/jobs/{job_id}")
async def coverage_interference_job_status(
    job_id: str,
    _key: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
) -> Dict:
    """Poll an async interference job. Inlines the result on completion."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("tower_id") != INTERFERENCE_JOB_SENTINEL:
        # Caller fed a PDF batch job_id into the interference endpoint.
        # Steer them to /jobs/{id} rather than leak the raw record.
        raise HTTPException(
            status_code=404,
            detail="Job is not an interference job; use GET /jobs/{id}",
        )
    out: Dict[str, object] = {
        "job_id": job_id,
        "status": job["status"],
        "n_candidates": job["total"],
    }
    if job["status"] == "failed":
        out["error"] = job.get("error", "unknown")
    if job["status"] == "completed":
        out["result_url"] = f"/coverage/interference/jobs/{job_id}/result"
    return out


@app.get("/coverage/interference/jobs/{job_id}/result")
async def coverage_interference_job_result(
    job_id: str,
    _key: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
) -> Dict:
    """Fetch the JSON result of a completed async interference job."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("tower_id") != INTERFERENCE_JOB_SENTINEL:
        raise HTTPException(status_code=404, detail="Not an interference job")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job['status']}; result not available yet",
        )
    payload = _load_interference_result_from_s3(job)
    if payload is None:
        raise HTTPException(status_code=410, detail="Result expired or unreadable")
    return payload


# ─────────────────────────────────────────────────────────────────────
# White-label / tenant branding (Enterprise)
# ─────────────────────────────────────────────────────────────────────

class TenantBranding(BaseModel):
    """Whitelisted tenant branding fields. Anything else is dropped on PUT."""

    company_name: Optional[str] = Field(default=None, max_length=120)
    logo_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="HTTPS URL of the tenant logo (PNG/SVG).",
    )
    primary_color: Optional[str] = Field(
        default=None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex colour, e.g. #1F4ED8.",
    )
    secondary_color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    frontend_url: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="HTTPS origin where the tenant's white-label UI is hosted. "
                    "Auto-allowed in CORS for that tenant's API key.",
    )
    support_email: Optional[str] = Field(default=None, max_length=200)
    favicon_url: Optional[str] = Field(default=None, max_length=2048)
    custom_css_url: Optional[str] = Field(default=None, max_length=2048)


def _validate_https_url(value: Optional[str], field: str) -> Optional[str]:
    """Reject http://, javascript:, data: and similar — only https:// is allowed."""
    if not value:
        return value
    v = value.strip()
    # OWASP A03: prevent script injection via crafted logo/frontend URLs
    # rendered into the SPA (script src, iframe src, …). Only https is
    # acceptable for a hosted white-label.
    if not v.lower().startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be an https:// URL",
        )
    return v


def _client_ip(request: Request) -> Optional[str]:
    """Extract the caller IP, honouring trusted proxy headers.

    The ALB and Caddy in front of the API both add ``X-Forwarded-For``
    and ``X-Real-IP``. We take the first IP in XFF (the original client)
    and fall back to ``request.client.host`` for direct connections.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # Take the first non-empty token.
        for part in xff.split(","):
            ip = part.strip()
            if ip:
                return ip[:64]
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return None


@app.get("/tenant/branding")
async def get_tenant_branding(key_data: Dict = Depends(verify_api_key)):
    """Return the calling tenant's branding overrides (or empty dict)."""
    rec = _key_store_db.lookup_key(key_data.get("api_key") or "") or {}
    branding = rec.get("branding") or {}
    return {
        "tier": key_data["tier"].value,
        "white_label_enabled": key_data["tier"] in (Tier.ENTERPRISE, Tier.ULTRA),
        "branding": branding,
    }


@app.put("/tenant/branding")
async def set_tenant_branding(
    branding: TenantBranding,
    request: Request,
    key_data: Dict = Depends(require_tier(Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Update the calling Enterprise tenant's branding (white-label).

    Replaces the stored branding blob with the supplied fields. URLs must
    be https. The tenant's ``frontend_url`` is auto-allowed in CORS so
    the white-label SPA can call this API from its own origin without an
    operator changing the CORS_ORIGINS env var.
    """
    api_key = key_data.get("api_key")
    if not api_key:
        # Should never happen — verify_api_key always sets api_key.
        raise HTTPException(status_code=500, detail="api_key not in key_data")

    payload = branding.model_dump(exclude_none=True)
    for field in ("logo_url", "frontend_url", "favicon_url", "custom_css_url"):
        if field in payload:
            payload[field] = _validate_https_url(payload[field], field)

    try:
        _key_store_db.set_branding(api_key, payload or None)
    except Exception:
        logger.exception("failed to persist tenant branding for %s", api_key[:12])
        raise HTTPException(status_code=500, detail="branding store unavailable")

    # Bust the CORS cache so the new frontend_url takes effect immediately.
    _tenant_origins_cache.clear()

    await _audit.log(
        api_key,
        "tenant.branding.update",
        actor_email=key_data.get("email") or key_data.get("owner"),
        tier=key_data["tier"].value,
        target="tenant.branding",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"fields": sorted(payload.keys())},
    )

    return {
        "status": "ok",
        "branding": payload,
    }


@app.delete("/tenant/branding")
async def delete_tenant_branding(
    request: Request,
    key_data: Dict = Depends(require_tier(Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Clear all branding overrides for the calling tenant."""
    api_key = key_data.get("api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="api_key not in key_data")
    _key_store_db.set_branding(api_key, None)
    _tenant_origins_cache.clear()
    await _audit.log(
        api_key,
        "tenant.branding.delete",
        actor_email=key_data.get("email") or key_data.get("owner"),
        tier=key_data["tier"].value,
        target="tenant.branding",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "ok"}


@app.get("/tenant/audit")
async def get_tenant_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    key_data: Dict = Depends(verify_api_key),
):
    """Return the calling tenant's recent audit log entries (newest first).

    Tenants only see their own rows; cross-tenant reads are not exposed
    via the public API. The endpoint is available to all tiers — every
    paying customer can audit their own activity for compliance.
    """
    api_key = key_data.get("api_key") or ""
    rows = _audit.recent_for_key(api_key, limit=limit)
    # Decode metadata_json so the response is one consistent shape.
    out = []
    for r in rows:
        meta_raw = r.get("metadata_json")
        try:
            meta = json.loads(meta_raw) if meta_raw else None
        except Exception:  # noqa: BLE001
            meta = None
        out.append({
            "id": r.get("id"),
            "ts": r.get("ts"),
            "action": r.get("action"),
            "target": r.get("target"),
            "actor_email": r.get("actor_email"),
            "tier": r.get("tier"),
            "ip": r.get("ip"),
            "user_agent": r.get("user_agent"),
            "metadata": meta,
        })
    return {"count": len(out), "entries": out}


# ─────────────────────────────────────────────────────────────────────
# Admin / sales-facing endpoints
# ─────────────────────────────────────────────────────────────────────
# Read-only aggregated views used by the sales dashboard at /admin/sales
# in the React frontend, and by the Grafana "Sales Overview" dashboard.
# Gated by an admin API key (env ``ADMIN_API_KEYS`` — comma-separated
# list of keys with full cross-tenant read access). No mutating ops.

_ADMIN_API_KEYS: set[str] = {
    k.strip() for k in os.getenv("ADMIN_API_KEYS", "").split(",") if k.strip()
}


def _admin_email_for(api_key: str) -> str:
    """Resolve a human-readable email for an admin api_key for audit logs.

    Falls back to the key prefix if the admin key isn't also a tenant
    (e.g. the bootstrap key set only via ``ADMIN_API_KEYS``).
    """
    try:
        rec = _key_store_db.lookup_key(api_key) or {}
    except Exception:
        rec = {}
    return rec.get("email") or rec.get("owner") or f"admin:{api_key[:12]}"


# ─────────────────────────────────────────────────────────────────────
# Step-up MFA + textual justification for sensitive admin endpoints
# ─────────────────────────────────────────────────────────────────────
# Holding an admin API key alone is not enough to impersonate a tenant
# or read their per-tenant detail panel. The operator must additionally:
#
#   1. Present a current TOTP code from an authenticator app whose secret
#      is registered for that admin (env ``ADMIN_TOTP_SECRETS`` or the
#      Docker secret ``admin_totp_secrets``, format
#      ``email1:base32secret1,email2:base32secret2``). The lookup key is
#      the value returned by ``_admin_email_for`` so it works for both
#      tenant-backed admins (real email) and bootstrap admins
#      (``admin:<prefix>``).
#   2. Provide a free-text ``justification`` explaining *why* (10-500
#      chars, e.g. "Customer ticket TT-1234 — investigating PDF render
#      failure"). The text is recorded verbatim in the audit metadata
#      so a future reviewer can challenge inappropriate access.
#
# The TOTP verifier is implemented in stdlib (RFC 6238, SHA-1, 6 digits,
# 30s step) — no extra runtime dependency, no external auth call. A
# 1-step window each side absorbs clock drift between the operator
# device and the server.

import base64 as _b64
import struct as _struct


def _load_admin_totp_secrets() -> Dict[str, str]:
    """Parse ``email1:secret1,email2:secret2`` into a dict.

    Reads the Docker secret file first, falling back to the env var so
    secret rotation matches the pattern used by other secrets.
    """
    raw = ""
    secret_file = "/run/secrets/admin_totp_secrets"
    try:
        if os.path.exists(secret_file):
            with open(secret_file, "r") as fh:
                raw = fh.read().strip()
    except Exception:  # noqa: BLE001
        raw = ""
    if not raw:
        raw = os.getenv("ADMIN_TOTP_SECRETS", "").strip()
    out: Dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        email, secret = entry.split(":", 1)
        email = email.strip().lower()
        secret = secret.strip().replace(" ", "").upper()
        if email and secret:
            out[email] = secret
    return out


_ADMIN_TOTP_SECRETS: Dict[str, str] = _load_admin_totp_secrets()


def _totp_at(secret_b32: str, counter: int) -> str:
    """Compute a 6-digit RFC 6238 TOTP for the given step counter."""
    # base32 secrets are sometimes stored with padding stripped; pad up
    # to a multiple of 8 chars before decoding.
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    try:
        key = _b64.b32decode(secret_b32 + pad, casefold=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid base32 TOTP secret") from exc
    msg = _struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = ((h[offset] & 0x7F) << 24
            | (h[offset + 1] & 0xFF) << 16
            | (h[offset + 2] & 0xFF) << 8
            | (h[offset + 3] & 0xFF))
    return f"{code % 1_000_000:06d}"


def _verify_admin_totp(admin_key: str, code: str) -> None:
    """Raise HTTPException unless ``code`` matches the admin's TOTP secret.

    Accepts the current step ±1 (≈90 s window) to absorb clock drift.
    Uses :func:`hmac.compare_digest` for constant-time comparison.
    """
    if not _ADMIN_TOTP_SECRETS:
        # Hard fail rather than silently bypassing — refusing to operate
        # without MFA configured is the safer default for sensitive routes.
        raise HTTPException(
            status_code=503,
            detail="step-up MFA not configured; set ADMIN_TOTP_SECRETS",
        )
    email = _admin_email_for(admin_key).lower()
    secret = _ADMIN_TOTP_SECRETS.get(email)
    if not secret:
        raise HTTPException(
            status_code=403,
            detail=f"step-up MFA not enrolled for admin '{email}'",
        )
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="TOTP code must be 6 digits")
    now_step = int(time.time()) // 30
    for delta in (-1, 0, 1):
        try:
            expected = _totp_at(secret, now_step + delta)
        except ValueError:
            raise HTTPException(status_code=500, detail="invalid TOTP secret on server")
        if hmac.compare_digest(expected, code):
            return
    raise HTTPException(status_code=403, detail="invalid TOTP code")


def _validate_justification(text: Optional[str]) -> str:
    """Trim and length-check the operator-supplied justification."""
    s = (text or "").strip()
    if len(s) < 10:
        raise HTTPException(
            status_code=400,
            detail="justification must be at least 10 characters",
        )
    if len(s) > 500:
        raise HTTPException(
            status_code=400,
            detail="justification must be at most 500 characters",
        )
    return s

# Approximate per-tier monthly revenue in BRL for MRR calculation.
# Mirrors frontend/src/Pricing.jsx; intentionally simple — the source of
# truth for billing remains Stripe.
_TIER_MRR_BRL = {
    "free": 0,
    "starter": 79,
    "pro": 349,
    "business": 1299,
    "enterprise": 1890,
    "ultra": 2900,
}


async def require_admin(request: Request, api_key: str = Security(api_key_header)) -> str:
    """Dependency: admin-only endpoints. Verifies the key is in ``ADMIN_API_KEYS``.

    Falls back to 403 (not 401) when a regular tenant key is presented so
    callers can distinguish "not authenticated" from "not authorized".
    """
    if not api_key:
        raise HTTPException(status_code=401, detail="X-API-Key required")
    if not _ADMIN_API_KEYS or api_key not in _ADMIN_API_KEYS:
        raise HTTPException(status_code=403, detail="admin scope required")
    return api_key


@app.get("/admin/sales/overview")
async def admin_sales_overview(
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict:
    """Sales-facing aggregated view: tenants by tier, MRR estimate, signups.

    Returns a single JSON document with everything the sales dashboard
    needs in one round-trip, so the React UI doesn't fan out across many
    requests. All numbers are derived from the production key store and
    audit log; no PII beyond company / billing email.
    """
    await _audit.log(
        admin_key,
        "admin.sales.overview.read",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target="admin.sales.overview",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    try:
        all_keys = _key_store_db.get_all_keys() or {}
    except Exception:
        logger.exception("admin_sales_overview: could not list keys")
        all_keys = {}

    # Per-tier counts and MRR (excluding demo + system keys).
    tier_counts: Dict[str, int] = {}
    mrr_brl = 0
    sso_enabled = 0
    white_label_enabled = 0
    for rec in all_keys.values():
        if not rec:
            continue
        if (rec.get("owner") or "").lower() == "system":
            continue
        if rec.get("demo"):
            continue
        tier = (rec.get("tier") or "free").lower()
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        mrr_brl += _TIER_MRR_BRL.get(tier, 0)
        if rec.get("sso_enabled"):
            sso_enabled += 1
        if (rec.get("branding") or {}).get("frontend_url"):
            white_label_enabled += 1

    # Recent signups (last 30 days) — sorted newest first.
    cutoff = time.time() - 30 * 86400
    recent: list[dict] = []
    for k, rec in all_keys.items():
        if not rec or not rec.get("created_at") or rec.get("created_at") < cutoff:
            continue
        recent.append({
            "api_key_prefix": (k or "")[:8],
            "tier": rec.get("tier"),
            "owner": rec.get("owner"),
            "email": rec.get("email"),
            "billing_cycle": rec.get("billing_cycle"),
            "created_at": rec.get("created_at"),
            "sso_enabled": bool(rec.get("sso_enabled")),
        })
    recent.sort(key=lambda r: r["created_at"] or 0, reverse=True)

    # Top tenants by audit-log activity (last 30 days).
    top_active: list[dict] = []
    try:
        rows = await asyncio.to_thread(_audit.top_actors, cutoff, 20)
        # Resolve api_key -> owner/tier/email.
        for row in rows:
            api_key = row.get("api_key")
            rec = all_keys.get(api_key) or {}
            top_active.append({
                "api_key_prefix": (api_key or "")[:8],
                "tier": rec.get("tier"),
                "owner": rec.get("owner"),
                "email": rec.get("email"),
                "events_30d": row.get("count", 0),
            })
    except Exception:
        logger.exception("admin_sales_overview: top_actors failed")

    return {
        "generated_at": time.time(),
        "totals": {
            "tenants": sum(tier_counts.values()),
            "mrr_brl": mrr_brl,
            "arr_brl": mrr_brl * 12,
            "sso_enabled": sso_enabled,
            "white_label_enabled": white_label_enabled,
        },
        "by_tier": [
            {"tier": t, "count": c, "mrr_brl": _TIER_MRR_BRL.get(t, 0) * c}
            for t, c in sorted(tier_counts.items(), key=lambda x: -x[1])
        ],
        "recent_signups": recent[:50],
        "top_active": top_active,
    }


@app.get("/admin/sales/tenants/{api_key_prefix}")
async def admin_sales_tenant_detail(
    api_key_prefix: str,
    request: Request,
    admin_key: str = Depends(require_admin),
    x_admin_totp: Optional[str] = Header(default=None, alias="X-Admin-TOTP"),
    x_admin_justification: Optional[str] = Header(
        default=None, alias="X-Admin-Justification",
    ),
) -> Dict:
    """Per-tenant sales detail — usage, billing cycle, SSO/white-label state."""
    if len(api_key_prefix) < 6:
        raise HTTPException(status_code=400, detail="api_key_prefix must be ≥6 chars")
    # Step-up MFA + justification — holding the admin key is not enough.
    _verify_admin_totp(admin_key, x_admin_totp or "")
    justification = _validate_justification(x_admin_justification)
    try:
        all_keys = _key_store_db.get_all_keys() or {}
    except Exception:
        logger.exception("admin_sales_tenant_detail: list_keys failed")
        all_keys = {}
    matches = [(k, rec) for k, rec in all_keys.items() if (k or "").startswith(api_key_prefix)]
    if not matches:
        raise HTTPException(status_code=404, detail="no tenant matches that prefix")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="prefix is ambiguous; use more characters")
    api_key, rec = matches[0]
    rec = rec or {}
    await _audit.log(
        admin_key,
        "admin.sales.tenant.read",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target=f"tenant:{api_key[:12]}",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={"justification": justification, "step_up": "totp"},
    )
    # Last 100 audit entries for this tenant.
    try:
        recent_audit = await asyncio.to_thread(_audit.recent_for_key, api_key, 100)
    except Exception:
        logger.exception("admin_sales_tenant_detail: audit lookup failed")
        recent_audit = []
    # Redact the raw api_key from each audit row — admins should use the
    # explicit POST /admin/impersonate endpoint to retrieve it (audited).
    for row in recent_audit:
        if "api_key" in row:
            row["api_key_prefix"] = (row.get("api_key") or "")[:12]
            row.pop("api_key", None)
    return {
        "api_key_prefix": api_key[:8],
        "tier": rec.get("tier"),
        "owner": rec.get("owner"),
        "email": rec.get("email"),
        "stripe_customer_id": rec.get("stripe_customer_id"),
        "stripe_subscription_id": rec.get("stripe_subscription_id"),
        "billing_cycle": rec.get("billing_cycle"),
        "created_at": rec.get("created_at"),
        "sso_enabled": bool(rec.get("sso_enabled")),
        "oauth_provider": rec.get("oauth_provider"),
        "branding": rec.get("branding"),
        "white_label_enabled": bool((rec.get("branding") or {}).get("frontend_url")),
        "recent_audit": recent_audit,
    }


class _ImpersonateRequest(BaseModel):
    """Step-up payload for POST /admin/impersonate/{api_key_prefix}."""
    totp: str = Field(..., min_length=6, max_length=6, description="6-digit TOTP code")
    justification: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Free-text reason for impersonation (recorded in audit log)",
    )


@app.post("/admin/impersonate/{api_key_prefix}")
async def admin_impersonate(
    api_key_prefix: str,
    body: _ImpersonateRequest,
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict:
    """Return the full API key for a tenant (support / impersonation).

    Intentionally a POST so it doesn't show up in browser-history GETs.
    Every call writes an audit row keyed to the impersonated tenant
    *and* a separate row keyed to the admin, so both sides of the
    impersonation are traceable.

    The returned ``api_key`` lets the operator make requests on the
    tenant's behalf for support purposes (e.g. paste it into a CLI to
    reproduce a user-reported bug). It does NOT change anything about
    the tenant's account.
    """
    if len(api_key_prefix) < 6:
        raise HTTPException(status_code=400, detail="api_key_prefix must be ≥6 chars")
    # Step-up MFA + justification — enforced *before* we even look up the
    # tenant, so a brute-force prefix scan can't piggy-back on this route.
    _verify_admin_totp(admin_key, body.totp)
    justification = _validate_justification(body.justification)
    try:
        all_keys = _key_store_db.get_all_keys() or {}
    except Exception:
        logger.exception("admin_impersonate: list_keys failed")
        all_keys = {}
    matches = [(k, rec) for k, rec in all_keys.items() if (k or "").startswith(api_key_prefix)]
    if not matches:
        raise HTTPException(status_code=404, detail="no tenant matches that prefix")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="prefix is ambiguous; use more characters")
    target_key, rec = matches[0]
    rec = rec or {}
    actor_email = _admin_email_for(admin_key)
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    # Audit row #1: appears in the *target tenant's* timeline so the
    # tenant can see (via support tooling) that an admin impersonated them.
    await _audit.log(
        target_key,
        "admin.impersonate.issued",
        actor_email=actor_email,
        tier=(rec.get("tier") or "unknown"),
        target=f"tenant:{target_key[:12]}",
        ip=ip,
        user_agent=ua,
        metadata={
            "admin_key_prefix": admin_key[:12],
            "justification": justification,
            "step_up": "totp",
        },
    )
    # Audit row #2: appears in the *admin's* own timeline.
    await _audit.log(
        admin_key,
        "admin.impersonate.read",
        actor_email=actor_email,
        tier="admin",
        target=f"tenant:{target_key[:12]}",
        ip=ip,
        user_agent=ua,
        metadata={"justification": justification, "step_up": "totp"},
    )
    return {
        "api_key": target_key,
        "tier": rec.get("tier"),
        "owner": rec.get("owner"),
        "email": rec.get("email"),
        "issued_at": time.time(),
        "expires_at": None,  # permanent — revoke by rotating the tenant's key
        "warning": (
            "This is the tenant's live API key. All actions taken with it "
            "are recorded in BOTH the tenant's audit log and yours."
        ),
    }


class _CacheInvalidateRequest(BaseModel):
    """Payload for POST /admin/cache/invalidate-towers."""
    tower_ids: List[str] = Field(
        ..., min_length=1, max_length=10_000,
        description="Tower IDs whose hop_cache entries should be invalidated",
    )
    reason: str = Field(
        default="satellite-change",
        max_length=100,
        description="Human-readable reason recorded in the audit log",
    )
    ttl_s: Optional[int] = Field(
        default=None, ge=60, le=90 * 24 * 3600,
        description="Optional override for the stale-marker TTL (seconds)",
    )


@app.post("/admin/cache/invalidate-towers")
async def admin_cache_invalidate_towers(
    body: _CacheInvalidateRequest,
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict:
    """Mark a list of tower IDs as stale in the shared hop_cache.

    The next ``plan_repeater`` call that touches any of those towers
    will bypass cache, recompute the hop costs from fresh terrain +
    model state, and clear the stale flags. Designed to close the
    loop with the satellite-change robot: when fresh imagery flags a
    site, that site's RF predictions can no longer be trusted.

    Idempotent. Returns the number of markers actually written
    (always 0 when the API runs without a Redis backend, since the
    in-memory LRU is process-local and cannot coordinate workers).
    """
    try:
        import hop_cache  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"hop_cache unavailable: {e}")

    written = hop_cache.mark_towers_stale(
        body.tower_ids,
        ttl_s=body.ttl_s,
        reason=body.reason,
    )
    await _audit.log(
        admin_key,
        "admin.cache.invalidate_towers",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target=f"hop_cache:{len(body.tower_ids)}",
        ip=_client_ip(request),
        metadata={"reason": body.reason, "written": written, "requested": len(body.tower_ids)},
    )
    return {
        "requested": len(body.tower_ids),
        "marked_stale": written,
        "reason": body.reason,
        "ttl_s": body.ttl_s,
    }


# ─────────────────────────────────────────────────────────────────────
# Internal Tier-1 upload (admin-scope blob store)
# ─────────────────────────────────────────────────────────────────────
# Restricted to ADMIN_API_KEYS (Tier-1). Used by internal tooling to
# stage CSV/JSON/PDF/ZIP artefacts in the configured object store
# (MinIO on-prem, S3 in SaaS) without going through the batch pipeline.

_INTERNAL_UPLOAD_MAX_BYTES = int(
    os.getenv("INTERNAL_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024))  # 100 MB
)
_INTERNAL_UPLOAD_ALLOWED_TYPES = {
    t.strip().lower()
    for t in os.getenv(
        "INTERNAL_UPLOAD_ALLOWED_TYPES",
        "text/csv,application/json,application/pdf,application/zip,"
        "application/octet-stream,application/geo+json,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ).split(",")
    if t.strip()
}
_INTERNAL_UPLOAD_PREFIX = os.getenv("INTERNAL_UPLOAD_PREFIX", "internal/uploads/")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Comma-separated CIDR allowlist (IPv4 / IPv6). Empty disables the check
# (useful for local dev / on-prem behind a private network). In SaaS prod
# this MUST be set to the office / VPN ranges.
_INTERNAL_UPLOAD_IP_ALLOWLIST = [
    c.strip()
    for c in os.getenv("INTERNAL_UPLOAD_IP_ALLOWLIST", "").split(",")
    if c.strip()
]
_INTERNAL_UPLOAD_PRESIGN_TTL = int(os.getenv("INTERNAL_UPLOAD_PRESIGN_TTL", "900"))


def _ip_in_allowlist(ip: Optional[str]) -> bool:
    if not _INTERNAL_UPLOAD_IP_ALLOWLIST:
        return True
    if not ip:
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in _INTERNAL_UPLOAD_IP_ALLOWLIST:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _sanitize_upload_filename(raw: str) -> str:
    """Reduce ``raw`` to a basename matching ``_SAFE_FILENAME_RE`` or raise."""
    name = os.path.basename((raw or "").strip()) or "file"
    # Strip any residual path-traversal artefacts.
    name = name.replace("\x00", "").lstrip(".") or "file"
    if len(name) > 200:
        name = name[-200:]
    if not _SAFE_FILENAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Filename must match [A-Za-z0-9._-]+ after basename stripping",
        )
    return name


@app.post("/internal/upload", status_code=201)
async def internal_upload(
    request: Request,
    upload: UploadFile = File(..., description="Arbitrary artefact to stage in object storage"),
    admin_key: str = Depends(require_admin),
) -> Dict:
    """Stage a caller-supplied blob in object storage. Tier-1 (admin) only.

    Streams the upload while computing SHA-256, enforces size + content-type
    allowlists, and writes the object under ``INTERNAL_UPLOAD_PREFIX`` with
    a date-partitioned, UUID-prefixed key. Filename is sanitised to a
    conservative allowlist after basename stripping to defeat path
    traversal. Records an audit row whether the upload succeeds or fails.
    Returns a short-lived presigned download URL when S3 is configured.
    """
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        await _audit.log(
            admin_key,
            "admin.internal.upload.denied_ip",
            actor_email=_admin_email_for(admin_key),
            tier="admin",
            target="ip_allowlist",
            ip=caller_ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "ip_not_in_allowlist"},
        )
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")

    content_type = (upload.content_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    if content_type not in _INTERNAL_UPLOAD_ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type '{content_type}' not allowed for internal upload",
        )
    safe_name = _sanitize_upload_filename(upload.filename or "file")

    sha = hashlib.sha256()
    size = 0
    chunks: List[bytes] = []
    chunk_size = 1024 * 1024  # 1 MB
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        size += len(chunk)
        if size > _INTERNAL_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds maximum size of {_INTERNAL_UPLOAD_MAX_BYTES} bytes",
            )
        sha.update(chunk)
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(status_code=422, detail="Empty upload rejected")

    digest = sha.hexdigest()
    now = datetime.now(timezone.utc)
    object_key = (
        f"{_INTERNAL_UPLOAD_PREFIX}{now.strftime('%Y/%m/%d')}/"
        f"{uuid.uuid4().hex}_{safe_name}"
    )

    try:
        from s3_storage import put_bytes as _put_bytes, get_presigned_url_for_key as _presign
        location = _put_bytes(object_key, b"".join(chunks), content_type=content_type)
        download_url = _presign(object_key, expires_in=_INTERNAL_UPLOAD_PRESIGN_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.exception("internal upload storage failure")
        await _audit.log(
            admin_key,
            "admin.internal.upload.failed",
            actor_email=_admin_email_for(admin_key),
            tier="admin",
            target=object_key,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            metadata={
                "filename": safe_name,
                "content_type": content_type,
                "size": size,
                "sha256": digest,
                "error": type(exc).__name__,
            },
        )
        raise HTTPException(status_code=500, detail="Storage backend unavailable")

    await _audit.log(
        admin_key,
        "admin.internal.upload",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target=object_key,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "filename": safe_name,
            "content_type": content_type,
            "size": size,
            "sha256": digest,
            "location": location,
            "presigned": bool(download_url),
        },
    )

    # Fire-and-forget webhook delivery for downstream consumers.
    try:
        import asyncio as _asyncio
        import webhook_store as _ws

        _hook_payload = {
            "object_key": object_key,
            "location": location,
            "size": size,
            "sha256": digest,
            "content_type": content_type,
            "filename": safe_name,
            "uploaded_at": now.isoformat(),
            "download_url": download_url,
            "download_url_expires_in": _INTERNAL_UPLOAD_PRESIGN_TTL if download_url else None,
        }
        _asyncio.create_task(_ws.dispatch("internal.upload.completed", _hook_payload))
    except Exception:  # noqa: BLE001
        logger.debug("upload webhook dispatch skipped", exc_info=True)

    return {
        "object_key": object_key,
        "location": location,
        "size": size,
        "sha256": digest,
        "content_type": content_type,
        "filename": safe_name,
        "uploaded_at": now.isoformat(),
        "download_url": download_url,
        "download_url_expires_in": _INTERNAL_UPLOAD_PRESIGN_TTL if download_url else None,
    }


# ─────────────────────────────────────────────────────────────────────
# Certified ANATEL filing validation (Tier-1)
# ─────────────────────────────────────────────────────────────────────
# Validates a batch of ANATEL ERB filings (CNPJ checksum, UF, BR bbox,
# licensed bands, EIRP / height bounds) and returns an HMAC-SHA256
# signed certificate so operators can later prove which rows the
# platform considered compliant at a given timestamp.

_ANATEL_VALIDATE_MAX_ROWS = int(os.getenv("ANATEL_VALIDATE_MAX_ROWS", "10000"))


class _AnatelValidateRequest(BaseModel):
    filings: List[Dict[str, Any]] = Field(
        ...,
        description="List of ANATEL filing rows. Required fields per row: "
                    "station_id, cnpj, operator, uf, municipio, lat, lon, "
                    "height_m, power_dbm, freq_mhz.",
    )
    issuer: Optional[str] = Field(
        default=None, max_length=64,
        description="Override the issuer string embedded in the certificate.",
    )


@app.post("/internal/anatel/validate-filing")
async def anatel_validate_filing(
    body: _AnatelValidateRequest,
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict[str, Any]:
    """Validate a batch of ANATEL ERB filings and return a signed certificate.

    Tier-1 (admin) only. Same IP allowlist as the internal upload endpoint.
    The response embeds an HMAC-SHA256 certificate over the canonical JSON
    of ``{validated_at, summary, results}`` so the operator can attach it
    to a regulatory submission and prove what was validated.
    """
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        await _audit.log(
            admin_key,
            "admin.anatel.validate.denied_ip",
            actor_email=_admin_email_for(admin_key),
            tier="admin",
            target="ip_allowlist",
            ip=caller_ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "ip_not_in_allowlist"},
        )
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")

    if len(body.filings) == 0:
        raise HTTPException(status_code=422, detail="filings list must not be empty")
    if len(body.filings) > _ANATEL_VALIDATE_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"filings list exceeds maximum of {_ANATEL_VALIDATE_MAX_ROWS} rows",
        )

    import anatel_validator as _av
    batch = _av.validate_batch(body.filings)
    payload = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": batch["summary"]["total"],
        "summary": batch["summary"],
        "results": batch["results"],
    }
    certificate = _av.certify(
        payload,
        issuer=body.issuer or "TELECOM-TOWER-POWER",
    )

    await _audit.log(
        admin_key,
        "admin.anatel.validate",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target=f"anatel.filing:{batch['summary']['total']}",
        ip=caller_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={
            "total": batch["summary"]["total"],
            "passed": batch["summary"]["passed"],
            "failed": batch["summary"]["failed"],
            "warnings": batch["summary"]["warnings"],
            "cert_sha256": certificate["sha256"],
            "cert_signed": certificate["signed"],
        },
    )

    # Fire-and-forget webhook delivery for downstream consumers.
    try:
        import asyncio as _asyncio
        import webhook_store as _ws

        _hook_payload = {
            "validated_at": payload["validated_at"],
            "row_count": payload["row_count"],
            "summary": payload["summary"],
            "issuer": body.issuer or "TELECOM-TOWER-POWER",
            "certificate": {
                "sha256": certificate["sha256"],
                "signed": certificate["signed"],
                "algorithm": certificate.get("algorithm"),
                "issuer": certificate.get("issuer"),
                "version": certificate.get("version"),
            },
            "format": "json",
        }
        _asyncio.create_task(_ws.dispatch("anatel.validation.completed", _hook_payload))
    except Exception:  # noqa: BLE001
        logger.debug("anatel json webhook dispatch skipped", exc_info=True)

    return {**payload, "certificate": certificate}


@app.post("/internal/anatel/validate-filing/pdf")
async def anatel_validate_filing_pdf(
    body: _AnatelValidateRequest,
    request: Request,
    admin_key: str = Depends(require_admin),
):
    """Same as ``/api/internal/anatel/validate-filing`` but returns a PDF.

    The PDF contains the summary, the HMAC certificate block, and a
    per-row results table (capped at 200 rows; the certificate's SHA-256
    still covers the full original payload).
    """
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        await _audit.log(
            admin_key,
            "admin.anatel.validate_pdf.denied_ip",
            actor_email=_admin_email_for(admin_key),
            tier="admin",
            target="ip_allowlist",
            ip=caller_ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "ip_not_in_allowlist"},
        )
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")

    if len(body.filings) == 0:
        raise HTTPException(status_code=422, detail="filings list must not be empty")
    if len(body.filings) > _ANATEL_VALIDATE_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"filings list exceeds maximum of {_ANATEL_VALIDATE_MAX_ROWS} rows",
        )

    import anatel_validator as _av
    from pdf_generator import build_anatel_validation_pdf

    batch = _av.validate_batch(body.filings)
    payload = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": batch["summary"]["total"],
        "summary": batch["summary"],
        "results": batch["results"],
    }
    issuer = body.issuer or "TELECOM-TOWER-POWER"
    certificate = _av.certify(payload, issuer=issuer)

    pdf_buf = build_anatel_validation_pdf(payload, certificate, issuer=issuer)

    await _audit.log(
        admin_key,
        "admin.anatel.validate_pdf",
        actor_email=_admin_email_for(admin_key),
        tier="admin",
        target=f"anatel.filing:{batch['summary']['total']}",
        ip=caller_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={
            "total": batch["summary"]["total"],
            "passed": batch["summary"]["passed"],
            "failed": batch["summary"]["failed"],
            "warnings": batch["summary"]["warnings"],
            "cert_sha256": certificate["sha256"],
            "cert_signed": certificate["signed"],
            "format": "pdf",
        },
    )

    filename = f"anatel-validation-{certificate['sha256'][:12]}.pdf"

    # Fire-and-forget webhook delivery for downstream consumers.
    try:
        import asyncio as _asyncio
        import webhook_store as _ws

        _hook_payload = {
            "validated_at": payload["validated_at"],
            "row_count": payload["row_count"],
            "summary": payload["summary"],
            "issuer": issuer,
            "certificate": {
                "sha256": certificate["sha256"],
                "signed": certificate["signed"],
                "algorithm": certificate.get("algorithm"),
                "issuer": certificate.get("issuer"),
                "version": certificate.get("version"),
            },
            "format": "pdf",
            "pdf_filename": filename,
        }
        _asyncio.create_task(_ws.dispatch("anatel.validation.completed.pdf", _hook_payload))
    except Exception:  # noqa: BLE001
        logger.debug("anatel pdf webhook dispatch skipped", exc_info=True)

    return StreamingResponse(
        pdf_buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Certificate-SHA256": certificate["sha256"],
            "X-Certificate-Signed": "true" if certificate["signed"] else "false",
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Enterprise outbound webhooks (Tier-1)
# ─────────────────────────────────────────────────────────────────────


class _WebhookCreateRequest(BaseModel):
    url: str = Field(..., description="HTTPS URL receiving signed POST events")
    events: List[str] = Field(..., min_length=1, description="Event names to subscribe to")
    secret: Optional[str] = Field(
        default=None, min_length=16, max_length=128,
        description="HMAC secret. If omitted, a 64-char hex secret is generated.",
    )
    description: Optional[str] = Field(default=None, max_length=200)
    enabled: bool = True


class _WebhookEnabledPatch(BaseModel):
    enabled: bool


@app.post("/internal/webhooks", status_code=201)
async def webhook_register(
    body: _WebhookCreateRequest,
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict[str, Any]:
    """Register an outbound webhook subscription. Admin + IP-allowlist gated.

    The returned ``secret`` is the only place the cleartext value is
    surfaced; subsequent ``GET`` calls return a redacted form. Store it
    safely on the consumer side.
    """
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        await _audit.log(
            admin_key, "admin.webhook.register.denied_ip",
            actor_email=_admin_email_for(admin_key), tier="admin",
            target="ip_allowlist", ip=caller_ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "ip_not_in_allowlist"},
        )
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")

    import webhook_store as _ws
    try:
        rec = _ws.register(
            url=body.url, events=body.events, secret=body.secret,
            description=body.description, enabled=body.enabled,
            created_by=_admin_email_for(admin_key) or "admin",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await _audit.log(
        admin_key, "admin.webhook.register",
        actor_email=_admin_email_for(admin_key), tier="admin",
        target=f"webhook:{rec['id']}", ip=caller_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={"url": rec["url"], "events": rec["events"], "enabled": rec["enabled"]},
    )
    # Echo the cleartext secret on creation only (never on GET/list).
    return rec


@app.get("/internal/webhooks")
async def webhook_list(
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict[str, Any]:
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")
    import webhook_store as _ws
    return {"webhooks": _ws.list_all(redact_secret=True), "valid_events": _ws.valid_events()}


@app.delete("/internal/webhooks/{webhook_id}", status_code=204)
async def webhook_delete(
    webhook_id: str,
    request: Request,
    admin_key: str = Depends(require_admin),
):
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")
    import webhook_store as _ws
    if not _ws.delete(webhook_id):
        raise HTTPException(status_code=404, detail="webhook not found")
    await _audit.log(
        admin_key, "admin.webhook.delete",
        actor_email=_admin_email_for(admin_key), tier="admin",
        target=f"webhook:{webhook_id}", ip=caller_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={},
    )
    return Response(status_code=204)


@app.patch("/internal/webhooks/{webhook_id}")
async def webhook_set_enabled(
    webhook_id: str,
    body: _WebhookEnabledPatch,
    request: Request,
    admin_key: str = Depends(require_admin),
) -> Dict[str, Any]:
    caller_ip = _client_ip(request)
    if not _ip_in_allowlist(caller_ip):
        raise HTTPException(status_code=403, detail="Caller IP not in allowlist")
    import webhook_store as _ws
    if not _ws.update_enabled(webhook_id, body.enabled):
        raise HTTPException(status_code=404, detail="webhook not found")
    await _audit.log(
        admin_key, "admin.webhook.update",
        actor_email=_admin_email_for(admin_key), tier="admin",
        target=f"webhook:{webhook_id}", ip=caller_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={"enabled": body.enabled},
    )
    rec = _ws.get(webhook_id) or {}
    item = dict(rec)
    sec = item.get("secret") or ""
    item["secret"] = f"***{sec[-4:]}" if sec else ""
    return item



# ─────────────────────────────────────────────────────────────────────
# Dynamic CORS reflection for tenant ``frontend_url``
# ─────────────────────────────────────────────────────────────────────
# CORSMiddleware uses a static allow list, so per-tenant white-label
# domains can't be configured via env. This middleware augments it: if
# the request carries an Origin matching some tenant's ``frontend_url``
# (and that tenant's API key is present in the request), we add the
# matching CORS headers so the browser accepts the response.

_tenant_origins_cache: Dict[str, str] = {}      # origin -> api_key (any tenant)
_tenant_origins_loaded_at: float = 0.0
_TENANT_ORIGINS_TTL = 300.0  # seconds


def _refresh_tenant_origins() -> None:
    """Reload the (origin -> api_key) map from the key store. Best-effort."""
    global _tenant_origins_loaded_at
    try:
        all_keys = _key_store_db.get_all_keys()
    except Exception:
        logger.debug("could not refresh tenant origins cache", exc_info=True)
        _tenant_origins_loaded_at = time.time()
        return
    _tenant_origins_cache.clear()
    for k, rec in all_keys.items():
        b = (rec or {}).get("branding") or {}
        origin = b.get("frontend_url")
        if not origin:
            continue
        # Strip trailing slash and any path so we match the browser's
        # ``Origin`` header byte-for-byte.
        try:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.scheme != "https" or not parsed.netloc:
                continue
            normalised = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            continue
        _tenant_origins_cache[normalised] = k
    _tenant_origins_loaded_at = time.time()


def _origin_for_tenant(origin: str, api_key: str) -> bool:
    """Return True iff ``origin`` belongs to the tenant identified by ``api_key``."""
    if not origin:
        return False
    if (time.time() - _tenant_origins_loaded_at) > _TENANT_ORIGINS_TTL:
        _refresh_tenant_origins()
    owner = _tenant_origins_cache.get(origin)
    return owner == api_key


@app.middleware("http")
async def tenant_cors_reflection(request: Request, call_next):
    """Reflect a tenant's white-label origin into the response CORS headers.

    Only takes effect when the request carries the matching tenant's
    ``X-API-Key`` and that tenant has stored a ``frontend_url``. Existing
    static CORS_ORIGINS handling is unaffected.
    """
    origin = request.headers.get("origin", "")
    api_key = request.headers.get("x-api-key", "")
    is_tenant_origin = bool(origin and api_key) and _origin_for_tenant(origin, api_key)

    # Short-circuit OPTIONS preflight ourselves so the static CORSMiddleware
    # (which only knows _allowed_origins) doesn't 400 a tenant origin.
    if is_tenant_origin and request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "X-API-Key, Content-Type, Authorization",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            },
        )

    response = await call_next(request)
    if is_tenant_origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        # Append, don't overwrite, the Vary header.
        prev_vary = response.headers.get("Vary", "")
        response.headers["Vary"] = (
            f"{prev_vary}, Origin" if prev_vary and "Origin" not in prev_vary else (prev_vary or "Origin")
        )
    return response


@app.post("/plan_repeater")
async def plan_repeater(tower_id: str, receiver: ReceiverInput, max_hops: int = 3, key_data: Dict = Depends(verify_api_key)):
    """Propose an optimized repeater chain using Dijkstra path search."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.model_dump())
    chain = await platform.plan_repeater_chain(tower, rx, max_hops)
    return {"repeater_chain": [asdict(t) for t in chain]}


# ---------------------------------------------------------------------------
# Async variant — submit a plan_repeater job and poll for the result.
# Intended for very large candidate sets (max_hops >= 4) where per-edge
# terrain fetches may still exceed HTTP timeouts even after asyncio.gather.
# ---------------------------------------------------------------------------
import repeater_jobs_store as _repeater_jobs_store

_REPEATER_JOBS_TTL_S = int(os.getenv("REPEATER_JOBS_TTL_S", "900"))  # 15 min
_REPEATER_JOBS_MAX = int(os.getenv("REPEATER_JOBS_MAX", "256"))

async def _reap_repeater_jobs() -> None:
    """Drop completed/failed repeater jobs older than TTL (no-op on Redis)."""
    await _repeater_jobs_store.get_store().reap(
        _REPEATER_JOBS_TTL_S, _REPEATER_JOBS_MAX
    )

async def _run_repeater_job(job_id: str, tower: Tower, rx: Receiver, max_hops: int) -> None:
    store = _repeater_jobs_store.get_store()
    try:
        chain = await platform.plan_repeater_chain(tower, rx, max_hops)
        await store.update(
            job_id,
            status="done",
            finished_at=time.time(),
            result={"repeater_chain": [asdict(t) for t in chain]},
        )
    except Exception as exc:  # noqa: BLE001 – surface in job state
        logger.exception("repeater job %s failed", job_id)
        await store.update(
            job_id,
            status="error",
            finished_at=time.time(),
            error=str(exc),
        )

@app.post("/plan_repeater/async")
async def plan_repeater_async(
    tower_id: str,
    receiver: ReceiverInput,
    max_hops: int = 3,
    key_data: Dict = Depends(verify_api_key),
):
    """Submit a repeater-planning job. Returns a job_id; poll
    ``GET /plan_repeater/jobs/{job_id}`` for progress and the final chain.

    Useful for large candidate sets (max_hops >= 4) where synchronous
    completion may exceed edge/CDN HTTP timeouts.
    """
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    if not (1 <= max_hops <= 6):
        raise HTTPException(status_code=400, detail="max_hops must be in [1, 6]")
    rx = Receiver(**receiver.model_dump())

    await _reap_repeater_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    await _repeater_jobs_store.get_store().create(
        job_id,
        {
            "job_id": job_id,
            "status": "running",
            "created_at": now,
            "tower_id": tower_id,
            "max_hops": max_hops,
            # OWASP A01 (IDOR) – lock job to caller's API-key owner so other
            # tenants can't read each other's repeater chains by guessing job_id.
            "owner": key_data.get("owner"),
        },
    )
    asyncio.create_task(_run_repeater_job(job_id, tower, rx, max_hops))
    return {
        "job_id": job_id,
        "status": "running",
        "poll_url": f"/plan_repeater/jobs/{job_id}",
    }

@app.get("/plan_repeater/jobs/{job_id}")
async def plan_repeater_job_status(
    job_id: str,
    key_data: Dict = Depends(verify_api_key),
):
    """Return the state (queued / running / done / error) and, when ready,
    the repeater_chain produced by ``POST /plan_repeater/async``."""
    job = await _repeater_jobs_store.get_store().get(job_id)
    # OWASP A01 (IDOR) – return 404 (not 403) when the job belongs to a
    # different owner so we don't leak existence of other tenants' jobs.
    if job is None or (job.get("owner") and job.get("owner") != key_data.get("owner")):
        raise HTTPException(status_code=404, detail="job not found or expired")
    return job

@app.get("/export_report")
async def export_report(request: Request, tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.FREE, Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA))):
    """Generate a professional PDF engineering report. Monthly quota per tier (Free: 5/mo)."""
    raw_key = request.headers.get("x-api-key", "")
    _enforce_pdf_quota(raw_key, key_data["tier"])
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(lat=lat, lon=lon, height_m=height_m, antenna_gain_dbi=antenna_gain)

    # Fetch real terrain profile (async)
    terrain_profile = await platform.elevation.get_profile(tower.lat, tower.lon, rx.lat, rx.lon)
    result = await platform.analyze_link(tower, rx, terrain_profile=terrain_profile)

    # Get primary frequency in MHz
    freq_mhz = tower.primary_freq_hz() / 1e6

    # Build PDF
    pdf_buffer = build_pdf_report(tower, rx, result, terrain_profile, freq_mhz)

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{tower_id}.pdf"}
    )

@app.get("/export_report/pdf")
async def export_report_pdf(request: Request, tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.FREE, Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA))):
    """Generate a professional PDF engineering report. Monthly quota per tier (Free: 5/mo)."""
    raw_key = request.headers.get("x-api-key", "")
    _enforce_pdf_quota(raw_key, key_data["tier"])
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(lat=lat, lon=lon, height_m=height_m, antenna_gain_dbi=antenna_gain)

    terrain_profile = await platform.elevation.get_profile(tower.lat, tower.lon, rx.lat, rx.lon)
    result = await platform.analyze_link(tower, rx, terrain_profile=terrain_profile)
    freq_mhz = tower.primary_freq_hz() / 1e6
    pdf_buffer = build_pdf_report(tower, rx, result, terrain_profile, freq_mhz)

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{tower_id}.pdf"}
    )

# ------------------------------------------------------------
# Batch PDF generation (Pro / Enterprise)
# ------------------------------------------------------------

@app.post("/batch_reports")
async def batch_reports(
    request: Request,
    tower_id: str,
    csv_file: UploadFile = File(...),
    receiver_height_m: float = 10.0,
    antenna_gain_dbi: float = 12.0,
    key_data: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Upload a CSV of receiver points (columns: lat,lon  and optionally
    height, gain) and download a ZIP of PDF reports – one per receiver.

    Small batches ( <= 100 rows) are processed synchronously.
    Larger batches are queued for the background worker and return a job_id.
    """
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")

    contents = await csv_file.read()
    try:
        text = contents.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(text)
    if not reader.fieldnames or "lat" not in reader.fieldnames or "lon" not in reader.fieldnames:
        raise HTTPException(
            status_code=400,
            detail="CSV must contain at least 'lat' and 'lon' columns",
        )

    receivers: List[Receiver] = []
    for row_num, row in enumerate(reader, start=2):
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (ValueError, KeyError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid lat/lon on CSV row {row_num}",
            )
        height = float(row.get("height", receiver_height_m) or receiver_height_m)
        gain = float(row.get("gain", antenna_gain_dbi) or antenna_gain_dbi)
        receivers.append(Receiver(lat=lat, lon=lon, height_m=height, antenna_gain_dbi=gain))

    if not receivers:
        raise HTTPException(status_code=400, detail="CSV contains no receiver rows")

    tier_batch_limit = TIER_LIMITS[key_data["tier"]]["max_batch_rows"]
    if len(receivers) > tier_batch_limit:
        raise HTTPException(
            status_code=400,
            detail=f"CSV has {len(receivers)} rows, exceeding the {key_data['tier'].value} "
                   f"tier limit of {tier_batch_limit}. Upgrade your plan or reduce the file.",
        )

    SYNC_BATCH_LIMIT = 100
    if len(receivers) > SYNC_BATCH_LIMIT:
        # Persist job to the database queue for the background worker
        job_id = str(uuid.uuid4())
        receivers_list = [
            {"lat": rx.lat, "lon": rx.lon,
             "height_m": rx.height_m, "antenna_gain_dbi": rx.antenna_gain_dbi}
            for rx in receivers
        ]
        receivers_json = json.dumps(receivers_list)
        _caller_key = request.headers.get("x-api-key", "")
        job_store.create_job(
            job_id=job_id,
            tower_id=tower_id,
            receivers_json=receivers_json,
            total=len(receivers),
            api_key=_caller_key,
        )

        # Publish to SQS if configured (serverless Lambda worker path).
        # Enterprise traffic goes to the high-priority queue (separate Lambda
        # function with reserved concurrency) so 10k-row jobs are not blocked
        # by Pro/Business work in the default queue.
        _tier_value = key_data["tier"].value
        _queue_url = _queue_for_tier(_tier_value)
        if _queue_url:
            _get_sqs().send_message(
                QueueUrl=_queue_url,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "tower_id": tower_id,
                    "tier": _tier_value,
                }),
            )

        await _audit.log(
            _caller_key,
            "batch.create",
            actor_email=key_data.get("email") or key_data.get("owner"),
            tier=_tier_value,
            target=f"job:{job_id}",
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            metadata={
                # tower_id is a competitive-intel signal (it geolocates
                # the tenant's planning targets); only persist a stable
                # per-tenant HMAC so the tenant can correlate their own
                # history while admins / backups / subpoenas see opaque
                # references.
                "tower_ref": _audit.hmac_target(tower_id, _caller_key),
                "rows": len(receivers),
                "queue": "priority" if _queue_url == SQS_QUEUE_URL_PRIORITY and _queue_url else (
                    "default" if _queue_url else "db-only"
                ),
            },
        )

        return {
            "job_id": job_id,
            "status": "queued",
            "total": len(receivers),
            "message": f"Batch too large for sync ({len(receivers)} rows). "
                       f"Job created. Poll GET /jobs/{job_id} for progress.",
        }

    freq_mhz = tower.primary_freq_hz() / 1e6
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, rx in enumerate(receivers):
            terrain = await platform.elevation.get_profile(
                tower.lat, tower.lon, rx.lat, rx.lon
            )
            result = await platform.analyze_link(tower, rx, terrain_profile=terrain)
            pdf_buf = build_pdf_report(tower, rx, result, terrain, freq_mhz)
            filename = f"report_{tower_id}_{idx + 1:03d}.pdf"
            zf.writestr(filename, pdf_buf.getvalue())

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=batch_reports_{tower_id}.zip"
        },
    )


# ------------------------------------------------------------
# Job status & download (persistent, DB-backed)
# ------------------------------------------------------------

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str, _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA))):
    """Poll the status of a background batch job (Pro/Enterprise only)."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    resp: Dict[str, object] = {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "tower_id": job["tower_id"],
    }
    if job["status"] == "completed":
        resp["download_url"] = f"/jobs/{job_id}/download"
    if job["status"] == "failed":
        resp["error"] = job["error"]
    return resp


@app.websocket("/jobs/{job_id}/ws")
async def job_progress_ws(websocket: WebSocket, job_id: str, token: str = Query(default="")):
    """WebSocket endpoint for live progress streaming of a batch job.

    Pushes JSON frames with {job_id, status, progress, total, download_url?, error?}
    every ~1 s until the job completes or fails, then closes the socket.

    Requires a valid API key via the ``token`` query parameter.
    """
    # Authenticate via query-param token
    key_data = API_KEYS.get(token)
    if key_data is None:
        dynamic = stripe_billing.lookup_key(token)
        if dynamic is None:
            await websocket.close(code=4001, reason="Invalid or missing API key")
            return

    # Validate job exists before accepting
    job = job_store.get_job(job_id)
    if job is None:
        await websocket.close(code=4004, reason="Job not found")
        return

    await websocket.accept()
    try:
        while True:
            job = job_store.get_job(job_id)
            if job is None:
                await websocket.send_json({"error": "Job disappeared"})
                break

            msg: Dict[str, object] = {
                "job_id": job_id,
                "status": job["status"],
                "progress": job["progress"],
                "total": job["total"],
            }
            if job["status"] == "completed":
                msg["download_url"] = f"/jobs/{job_id}/download"
            if job["status"] == "failed":
                msg["error"] = job.get("error", "Unknown error")

            await websocket.send_json(msg)

            # Terminal states → close gracefully
            if job["status"] in ("completed", "failed"):
                break

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass  # client left; nothing to clean up
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/jobs/{job_id}/download")
async def download_job_result(job_id: str, _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA))):
    """Download the ZIP file produced by a completed batch job (Pro/Enterprise only).

    If the result is stored in S3, returns a redirect to a presigned URL.
    If stored locally, streams the file directly.
    """
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job['status']}; cannot download yet",
        )
    result_path = job.get("result_path")
    if not result_path:
        raise HTTPException(status_code=410, detail="Result expired; please resubmit")

    # S3-backed result → redirect to presigned URL
    if result_path.startswith("s3://"):
        from s3_storage import get_presigned_url
        presigned = get_presigned_url(job_id)
        if presigned:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=presigned, status_code=307)
        # Fallback: try to stream from S3 directly
        from s3_storage import download_result
        data = download_result(job_id)
        if data is None:
            raise HTTPException(status_code=410, detail="Result expired; please resubmit")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=batch_reports_{job['tower_id']}.zip"
            },
        )

    # Local file path
    if not os.path.exists(result_path):
        raise HTTPException(status_code=410, detail="Result expired; please resubmit")

    def _stream_file():
        with open(result_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        _stream_file(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=batch_reports_{job['tower_id']}.zip"
        },
    )


# ------------------------------------------------------------
# Self-service signup & Stripe billing
# ------------------------------------------------------------

class SignupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    captcha_token: Optional[str] = Field(
        None,
        max_length=2048,
        description="Cloudflare Turnstile token from the signup page. "
                    "Required when TURNSTILE_SECRET_KEY is set on the server.",
    )

class CheckoutRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    tier: str = Field(..., pattern="^(starter|pro|business|enterprise)$")
    billing_cycle: str = Field("monthly", pattern="^(monthly|annual)$")
    country: Optional[str] = Field(
        None,
        min_length=2, max_length=2,
        description="ISO 3166-1 alpha-2 country code for SRTM tile pre-download (enterprise only)",
    )


# ─────────────────────────────────────────────────────────────────
# /signup/free abuse controls
#
# 1. Per-IP rate limiting (sliding window, in-process). Cheap defence
#    against single-host bots — real distributed abuse is mitigated by
#    Turnstile below.
# 2. Optional Cloudflare Turnstile verification. Enabled when
#    `TURNSTILE_SECRET_KEY` is set; otherwise the limiter alone protects
#    the endpoint and the field is ignored.
#
# Per-IP limit defaults: 5 signups / hour / IP. Tuned via env so we can
# loosen for a launch event without a redeploy.
# Accepted env values: a positive integer, or one of {"0", "off",
# "none", "unlimited", "disabled"} (case-insensitive) to disable.
# ─────────────────────────────────────────────────────────────────
def _parse_signup_free_rph(raw: str) -> int:
    """Return the per-IP/hour limit. 0 == disabled (no enforcement)."""
    raw = (raw or "").strip().lower()
    if raw in {"", "0", "off", "none", "unlimited", "disabled", "false"}:
        return 0
    try:
        n = int(raw)
        return n if n > 0 else 0
    except ValueError:
        # Fail-safe: keep the default rather than crashing the app.
        return 5


_SIGNUP_FREE_RPH = _parse_signup_free_rph(
    os.getenv("SIGNUP_FREE_RATE_LIMIT_PER_HOUR", "5")
)
_SIGNUP_FREE_WINDOW_S = 3600.0
_signup_ip_buckets: Dict[str, collections.deque] = {}

_TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

SIGNUP_ABUSE_REJECTIONS = Counter(
    "signup_free_abuse_rejections_total",
    "Free-signup attempts rejected for abuse",
    labelnames=["reason"],  # ip_rate_limit | turnstile_missing | turnstile_invalid
)


def _check_signup_ip_rate_limit(ip: Optional[str]) -> None:
    """Raise 429 if `ip` has exceeded SIGNUP_FREE_RATE_LIMIT_PER_HOUR.

    Missing IP → no enforcement (behind a misconfigured proxy is annoying
    but should never lock everyone out). RPH==0 → disabled entirely.
    """
    if not ip or _SIGNUP_FREE_RPH <= 0:
        return
    now = time.monotonic()
    bucket = _signup_ip_buckets.setdefault(ip, collections.deque())
    while bucket and bucket[0] <= now - _SIGNUP_FREE_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= _SIGNUP_FREE_RPH:
        SIGNUP_ABUSE_REJECTIONS.labels(reason="ip_rate_limit").inc()
        retry_after = int(_SIGNUP_FREE_WINDOW_S - (now - bucket[0]))
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many signup attempts from this IP "
                f"({_SIGNUP_FREE_RPH}/hour). Try again later."
            ),
            headers={"Retry-After": str(max(retry_after, 1))},
        )
    bucket.append(now)
    # Periodic sweep of stale IPs (cheap O(n) when bucket grows large).
    if len(_signup_ip_buckets) > 10_000:
        cutoff = now - _SIGNUP_FREE_WINDOW_S
        for k, v in list(_signup_ip_buckets.items()):
            if not v or v[-1] <= cutoff:
                _signup_ip_buckets.pop(k, None)


async def _verify_turnstile(token: Optional[str], ip: Optional[str]) -> None:
    """Verify a Cloudflare Turnstile token. No-op when TURNSTILE_SECRET_KEY
    is unset (allows local dev / preview to keep working)."""
    if not _TURNSTILE_SECRET_KEY:
        return
    if not token:
        SIGNUP_ABUSE_REJECTIONS.labels(reason="turnstile_missing").inc()
        raise HTTPException(status_code=400, detail="CAPTCHA token missing.")
    payload = {
        "secret": _TURNSTILE_SECRET_KEY,
        "response": token[:2048],  # Turnstile tokens are <2KB; cap defensively.
    }
    if ip:
        payload["remoteip"] = ip
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            async with session.post(_TURNSTILE_VERIFY_URL, data=payload) as resp:
                data = await resp.json(content_type=None)
    except Exception:  # pragma: no cover - network noise
        # Fail-closed: if Cloudflare is unreachable we cannot trust the token.
        SIGNUP_ABUSE_REJECTIONS.labels(reason="turnstile_invalid").inc()
        raise HTTPException(status_code=503, detail="CAPTCHA verifier unavailable.")
    if not data.get("success"):
        SIGNUP_ABUSE_REJECTIONS.labels(reason="turnstile_invalid").inc()
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed.")


@app.post("/signup/free", status_code=201)
async def signup_free(body: SignupRequest, request: Request):
    """Register a free-tier account and receive an API key instantly.

    Rate-limited to 5 signups/hour/IP and (when configured) gated by
    Cloudflare Turnstile to prevent automated free-key farming, which
    would otherwise be an open faucet against per-call SRTM and Bedrock
    costs.
    """
    ip = _client_ip(request)
    _check_signup_ip_rate_limit(ip)
    await _verify_turnstile(body.captcha_token, ip)
    try:
        result = stripe_billing.register_free_user(body.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await _audit.log(
        result["api_key"],
        "key.issue.free",
        actor_email=result["email"],
        tier=result["tier"],
        target=f"key:{result['api_key'][:12]}",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "api_key": result["api_key"],
        "tier": result["tier"],
        "email": result["email"],
        "message": "Free account created. Include your API key in the X-API-Key header.",
    }


# ─── SSO / OIDC ─────────────────────────────────────────────────────────

class SsoExchangeRequest(BaseModel):
    """Body of POST /auth/sso. ``id_token`` is the Cognito-issued JWT."""
    id_token: str = Field(..., min_length=20, max_length=8192,
                          description="OIDC ID token (Cognito 'id_token').")
    provider: str = Field("cognito", min_length=2, max_length=20,
                          description="IdP identifier; only 'cognito' is supported today.")


@app.get("/signup/config")
async def signup_config():
    """Public hint for the signup form: whether CAPTCHA is required and
    which Turnstile site key to render. Never returns the secret key."""
    site_key = os.getenv("TURNSTILE_SITE_KEY", "").strip()
    return {
        "captcha": {
            "required": bool(_TURNSTILE_SECRET_KEY),
            "provider": "turnstile" if _TURNSTILE_SECRET_KEY else None,
            "site_key": site_key or None,
        },
        "signup_free_per_ip_per_hour": _SIGNUP_FREE_RPH,
    }


@app.get("/auth/sso/config")
async def sso_config():
    """Public discovery hint for the frontend (which IdP, which client)."""
    cfg = _sso.get_idp("cognito")
    if not cfg:
        return {"enabled": False}
    payload = {
        "enabled": True,
        "provider": cfg.name,
        "issuer": cfg.issuer,
        "audience": cfg.audience,
    }
    # Hosted UI URLs (only emitted when the server-side code exchange
    # endpoint is fully configured — otherwise the SPA can't use them).
    domain = os.getenv("COGNITO_DOMAIN", "").strip().rstrip("/")
    if domain and _sso.is_oauth_callback_configured():
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        payload["hosted_ui"] = {
            "authorize_url": f"{domain}/oauth2/authorize",
            "logout_url": f"{domain}/logout",
            "client_id": cfg.audience,
            "scope": "openid email profile",
        }
    return payload


class SsoCallbackRequest(BaseModel):
    """Body of POST /auth/sso/callback. SPA forwards the OAuth code here so
    the client_secret never leaves the server."""
    code: str = Field(..., min_length=4, max_length=2048,
                      description="OAuth2 authorization code from Cognito redirect.")
    redirect_uri: str = Field(..., min_length=8, max_length=512,
                              description="Must match the redirect_uri used on /authorize.")
    provider: str = Field("cognito", min_length=2, max_length=20)


@app.post("/auth/sso/callback")
async def sso_callback(body: SsoCallbackRequest, request: Request):
    """Server-side OAuth2 code → id_token → api_key exchange.

    The SPA hits ``/auth/callback`` after Cognito redirects with ``?code=``,
    forwards the code to this endpoint, and receives an api_key. The
    client_secret stays on the server. The id_token is verified with the
    same code path as :func:`sso_exchange`.
    """
    if not _sso.is_sso_configured():
        raise HTTPException(status_code=503, detail="SSO is not configured on this server")
    if not _sso.is_oauth_callback_configured():
        raise HTTPException(status_code=503, detail="SSO callback is not configured on this server")
    try:
        id_token = _sso.exchange_code_for_id_token(
            body.code, body.redirect_uri, provider=body.provider,
        )
    except _sso.SsoTokenError as exc:
        logger.info("sso /auth/sso/callback rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Authorization code rejected")
    # Reuse the exchange path so the audit log + idempotency stay identical.
    return await sso_exchange(SsoExchangeRequest(id_token=id_token,
                                                 provider=body.provider), request)


@app.post("/auth/sso")
async def sso_exchange(body: SsoExchangeRequest, request: Request):
    """Exchange an IdP-issued ID token for a long-lived TTP API key.

    The token is fully verified (signature, issuer, audience, expiry,
    token_use) before any DB write. On first login a fresh free-tier key
    is minted; subsequent logins return the same key. The api_key row is
    stamped with (oauth_provider, oauth_subject) so future requests can
    use ``Authorization: Bearer <id_token>`` directly.
    """
    if not _sso.is_sso_configured():
        raise HTTPException(status_code=503, detail="SSO is not configured on this server")
    try:
        claims = _sso.verify_id_token(body.id_token, provider=body.provider)
    except _sso.SsoTokenError as exc:
        logger.info("sso /auth/sso rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid SSO token")

    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        # Cognito User Pool may emit "cognito:username" but no email when
        # the pool was configured without that attribute. Surface a clear
        # 400 rather than minting a key with a synthetic email.
        raise HTTPException(status_code=400, detail="ID token has no 'email' claim")

    try:
        result = stripe_billing.register_or_get_sso_user(
            email=email,
            provider=body.provider,
            subject=sub,
            default_tier="free",
        )
    except Exception:  # noqa: BLE001
        logger.exception("register_or_get_sso_user failed")
        raise HTTPException(status_code=500, detail="Could not provision SSO key")

    api_key = result["api_key"]
    created = bool(result.get("_created"))
    await _audit.log(
        api_key,
        "auth.sso.exchange" if not created else "key.issue.sso",
        actor_email=email,
        tier=result.get("tier"),
        target=f"key:{api_key[:12]}",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "provider": body.provider,
            "sub": sub,
            "created": created,
        },
    )
    return {
        "api_key": api_key,
        "tier": result.get("tier"),
        "email": email,
        "sso_enabled": True,
        "created": created,
    }

# ─── SAML 2.0 (Okta / Azure AD) ────────────────────────────────────────

@app.get("/auth/saml/config")
async def saml_config():
    """Discovery hint for the SPA: which IdPs are wired and the SP URLs."""
    if not _saml.is_saml_configured():
        return {"enabled": False}
    try:
        return {
            "enabled": True,
            "providers": _saml.configured_idps(),
            "sp_entity_id": _saml.sp_entity_id(),
            "acs_url": _saml.sp_acs_url(),
            "login_url": "/auth/saml/login",
            "metadata_url": "/auth/saml/metadata",
        }
    except _saml.SamlError as exc:
        logger.warning("saml /auth/saml/config misconfigured: %s", exc)
        raise HTTPException(status_code=503, detail="SAML SP base URL is not configured")


@app.get("/auth/saml/metadata")
async def saml_metadata():
    """Return the SP SAML metadata XML for IdP onboarding."""
    if not _saml.is_saml_configured():
        raise HTTPException(status_code=503, detail="SAML is not configured on this server")
    try:
        xml = _saml.sp_metadata_xml()
    except _saml.SamlError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return Response(content=xml, media_type="application/samlmetadata+xml")


@app.get("/auth/saml/login")
async def saml_login(idp: str = "okta", relay_state: Optional[str] = None):
    """SP-initiated SAML login.

    Builds an AuthnRequest and 302-redirects the browser to the IdP SSO
    URL via the HTTP-Redirect binding. ``idp`` is one of the configured
    providers from ``GET /auth/saml/config``. ``relay_state`` is optional
    and echoed back by the IdP — the SPA can use it to remember the
    post-login destination.
    """
    cfg = _saml.get_idp(idp)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"SAML IdP '{idp}' is not configured")
    try:
        url, _req_id = _saml.build_authn_request(
            cfg, relay_state=relay_state or _saml.make_relay_state(),
        )
    except _saml.SamlError as exc:
        logger.warning("saml /auth/saml/login failed: %s", exc)
        raise HTTPException(status_code=503, detail="SAML SP base URL is not configured")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=url, status_code=302)


@app.post("/auth/saml/callback")
async def saml_callback(request: Request, idp: str = "okta"):
    """SAML Assertion Consumer Service (ACS).

    The IdP POSTs ``SAMLResponse`` (form-encoded, base64) here after the
    user authenticates. We verify the signature, audience and timing,
    extract the email, then issue/return the tenant API key via the
    same path used by ``POST /auth/sso``.
    """
    cfg = _saml.get_idp(idp)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"SAML IdP '{idp}' is not configured")
    form = await request.form()
    saml_response = form.get("SAMLResponse")
    relay_state = form.get("RelayState") or None
    if not isinstance(saml_response, str) or not saml_response:
        raise HTTPException(status_code=400, detail="Missing SAMLResponse")

    try:
        claims = _saml.parse_and_verify_response(saml_response, cfg)
    except _saml.SamlError as exc:
        logger.info("saml /auth/saml/callback rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid SAML response")

    email = claims["email"]
    name_id = claims.get("name_id") or email
    try:
        result = stripe_billing.register_or_get_sso_user(
            email=email,
            provider=f"saml:{idp}",
            subject=name_id,
            default_tier="free",
        )
    except Exception:  # noqa: BLE001
        logger.exception("register_or_get_sso_user failed (saml)")
        raise HTTPException(status_code=500, detail="Could not provision SSO key")

    api_key = result["api_key"]
    created = bool(result.get("_created"))
    await _audit.log(
        api_key,
        "auth.saml.exchange" if not created else "key.issue.saml",
        actor_email=email,
        tier=result.get("tier"),
        target=f"key:{api_key[:12]}",
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "idp": idp,
            "name_id": name_id,
            "session_index": claims.get("session_index"),
            "created": created,
        },
    )
    return {
        "api_key": api_key,
        "tier": result.get("tier"),
        "email": email,
        "sso_enabled": True,
        "created": created,
        "relay_state": relay_state,
        "idp": f"saml:{idp}",
    }


@app.post("/signup/checkout")
async def signup_checkout(body: CheckoutRequest):
    """
    Create a Stripe Checkout Session for a paid plan.
    Returns the Checkout URL the client should redirect to.
    For enterprise plans, pass *country* to pre-download SRTM elevation tiles.
    """
    try:
        url = stripe_billing.create_checkout_session(body.email, body.tier, body.country, body.billing_cycle)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Stripe checkout error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))
    return {"checkout_url": url}

@app.post("/stripe/webhook")
@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    """
    Receive Stripe webhook events (checkout.session.completed,
    customer.subscription.deleted, etc.).
    """
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        result = stripe_billing.handle_webhook_event(payload, sig)
    except stripe_billing.stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return result

@app.get("/signup/success")
async def signup_success(session_id: str):
    """
    After Stripe Checkout, the frontend redirects here with session_id.
    Returns the provisioned API key so the user can start using the API.
    """
    try:
        info = stripe_billing.retrieve_key_from_checkout_session(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "api_key": info["api_key"],
        "tier": info["tier"],
        "email": info["email"],
        "message": "Payment confirmed. Include your API key in the X-API-Key header.",
    }

class KeyLookupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)

# ---- IP-based rate limiter for unauthenticated signup endpoints ----
_signup_rate_buckets: Dict[str, collections.deque] = {}
_SIGNUP_RATE_LIMIT = 10    # requests per window
_SIGNUP_RATE_WINDOW = 3600  # 1 hour

def _check_signup_rate_limit(request: Request):
    """Rate-limit signup endpoints by client IP (10 req/hour)."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _signup_rate_buckets.setdefault(client_ip, collections.deque())
    while bucket and bucket[0] <= now - _SIGNUP_RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _SIGNUP_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many signup requests. Try again later.",
        )
    bucket.append(now)

@app.post("/signup/status")
async def signup_status(body: KeyLookupRequest, request: Request):
    """
    Look up an existing API key by email address.
    Returns a masked key, tier, and account status.
    """
    _check_signup_rate_limit(request)
    info = stripe_billing.get_key_info_for_email(body.email)
    if info is None:
        raise HTTPException(status_code=404, detail="No account found for this email")
    raw_key = info["api_key"]
    masked = raw_key[:8] + "…" + raw_key[-4:] if len(raw_key) > 12 else raw_key
    return {
        "api_key": info["api_key"],
        "api_key_masked": masked,
        "tier": info["tier"],
        "email": info["email"],
        "has_subscription": info.get("stripe_subscription_id") is not None,
    }

# ------------------------------------------------------------
# Customer self-service portal
# ------------------------------------------------------------

@app.get("/portal/profile")
async def portal_profile(
    request: Request,
    key_data: Dict = Depends(verify_api_key),
):
    """Return the caller's profile: masked API key, tier, limits, and account info."""
    raw_key = request.headers.get("x-api-key", "")
    masked = raw_key[:8] + "…" + raw_key[-4:] if len(raw_key) > 12 else raw_key
    tier = key_data["tier"]
    limits = TIER_LIMITS[tier]
    info = stripe_billing.lookup_key(raw_key) or {}
    return {
        "api_key_masked": masked,
        "tier": tier.value,
        "email": info.get("email") or key_data.get("owner", ""),
        "limits": {
            "requests_per_min": limits["requests_per_min"],
            "max_towers": limits["max_towers"],
            "max_batch_rows": limits["max_batch_rows"],
            "pdf_export": limits["pdf_export"],
        },
        "towers_created": _towers_created_per_key.get(raw_key, 0),
        "has_subscription": bool(info.get("stripe_subscription_id")),
        "created": info.get("created"),
    }


@app.get("/portal/usage")
async def portal_usage(
    request: Request,
    key_data: Dict = Depends(verify_api_key),
):
    """Return usage statistics for the caller's API key."""
    raw_key = request.headers.get("x-api-key", "")
    tier = key_data["tier"]
    limits = TIER_LIMITS[tier]
    usage = _usage_counters.get(raw_key, {"requests": 0, "since": time.time()})
    bucket = _rate_buckets.get(raw_key, collections.deque())
    # Count requests in current window
    now = time.monotonic()
    current_window = sum(1 for ts in bucket if ts > now - 60.0)
    return {
        "requests_total": usage["requests"],
        "tracking_since": usage["since"],
        "requests_current_minute": current_window,
        "rate_limit": limits["requests_per_min"],
        "towers_created": _towers_created_per_key.get(raw_key, 0),
        "towers_limit": limits["max_towers"],
    }


@app.get("/portal/jobs")
async def portal_jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    key_data: Dict = Depends(verify_api_key),
):
    """Return the caller's batch jobs (most recent first)."""
    raw_key = request.headers.get("x-api-key", "")
    jobs = job_store.list_jobs_by_api_key(raw_key, limit=limit)
    return {
        "jobs": [
            {
                "id": j["id"],
                "status": j["status"],
                "progress": j["progress"],
                "total": j["total"],
                "tower_id": j["tower_id"],
                "result_path": j.get("result_path"),
                "error": j.get("error"),
                "created_at": j["created_at"],
                "updated_at": j["updated_at"],
            }
            for j in jobs
        ],
        "count": len(jobs),
    }


@app.get("/portal/billing")
async def portal_billing(
    request: Request,
    key_data: Dict = Depends(verify_api_key),
):
    """Return billing information from Stripe for the caller."""
    raw_key = request.headers.get("x-api-key", "")
    info = stripe_billing.lookup_key(raw_key) or {}
    customer_id = info.get("stripe_customer_id")

    result = {
        "tier": key_data["tier"].value,
        "has_subscription": bool(info.get("stripe_subscription_id")),
        "stripe_customer_id": customer_id,
        "invoices": [],
    }

    # Fetch recent invoices from Stripe if customer exists
    if customer_id and stripe_billing.STRIPE_SECRET_KEY:
        try:
            invoices = stripe_billing.stripe.Invoice.list(
                customer=customer_id, limit=10
            )
            result["invoices"] = [
                {
                    "id": inv.id,
                    "amount_due": inv.amount_due,
                    "amount_paid": inv.amount_paid,
                    "currency": inv.currency,
                    "status": inv.status,
                    "created": inv.created,
                    "invoice_url": inv.hosted_invoice_url,
                    "pdf_url": inv.invoice_pdf,
                }
                for inv in invoices.auto_paging_iter()
            ]
        except Exception as exc:
            logger.warning("Failed to fetch Stripe invoices for %s: %s", customer_id, exc)

    return result


# ------------------------------------------------------------
# SRTM tile management (enterprise)
# ------------------------------------------------------------

class PrefetchRequest(BaseModel):
    country: str = Field(..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2")

@app.get("/srtm/status/{country}")
async def srtm_tile_status(
    country: str,
    _key: Dict = Depends(require_tier(Tier.ENTERPRISE, Tier.ULTRA)),
):
    """Report SRTM tile availability for a country (enterprise only)."""
    from srtm_prefetch import tile_status, COUNTRY_BOUNDS
    code = country.upper()
    if code not in COUNTRY_BOUNDS:
        raise HTTPException(
            status_code=404,
            detail=f"No bounding box for '{code}'. Available: {sorted(COUNTRY_BOUNDS)}",
        )
    return tile_status(code)

@app.post("/srtm/prefetch")
async def srtm_prefetch(
    body: PrefetchRequest,
    _key: Dict = Depends(require_tier(Tier.ENTERPRISE, Tier.ULTRA)),
):
    """
    Start background download of SRTM tiles for a country (enterprise only).
    Returns immediately; use GET /srtm/status/{country} to track progress.
    """
    from srtm_prefetch import prefetch_country_async, COUNTRY_BOUNDS
    code = body.country.upper()
    if code not in COUNTRY_BOUNDS:
        raise HTTPException(
            status_code=404,
            detail=f"No bounding box for '{code}'. Available: {sorted(COUNTRY_BOUNDS)}",
        )
    prefetch_country_async(code)
    return {"status": "started", "country": code}

# ------------------------------------------------------------
# Amazon Bedrock AI Playground
# ------------------------------------------------------------

class BedrockChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000, description="User prompt")
    model_id: Optional[str] = Field(None, description="Bedrock model ID override")
    max_tokens: Optional[int] = Field(None, ge=1, le=4096, description="Max response tokens")
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0, description="Sampling temperature")
    context: Optional[str] = Field(None, max_length=8000, description="Analysis context JSON")


@app.post("/bedrock/chat")
async def bedrock_chat(
    body: BedrockChatRequest,
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """
    Send a prompt to the configured LLM backend (Amazon Bedrock or local
    Ollama/Llama-3, selected via LLM_PROVIDER) and return the generated
    response.
    Requires PRO or ENTERPRISE tier.
    """
    from llm_provider import invoke_model
    try:
        result = invoke_model(
            prompt=body.prompt,
            model_id=body.model_id,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
            context=body.context,
        )
        return result
    except Exception as exc:
        logger.error("Bedrock chat error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Bedrock model error: {exc}")


@app.get("/bedrock/models")
async def bedrock_models(
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """List available foundation models for the AI playground (active backend)."""
    from llm_provider import list_available_models
    try:
        models = list_available_models()
        return {"models": models}
    except Exception as exc:
        logger.error("Bedrock list models error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Bedrock error: {exc}")


class BedrockScenarioRequest(BaseModel):
    scenarios: List[Dict] = Field(..., min_length=2, max_length=10, description="List of scenario dicts to compare")
    question: Optional[str] = Field(None, max_length=4000, description="Optional custom question")
    model_id: Optional[str] = Field(None, description="Bedrock model ID override")
    max_tokens: Optional[int] = Field(None, ge=1, le=4096)
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)


@app.post("/bedrock/compare")
async def bedrock_compare_scenarios(
    body: BedrockScenarioRequest,
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """
    Compare multiple RF scenarios using AI analysis.
    Accepts 2-10 scenarios (e.g. different frequencies, antenna heights)
    and returns an engineering comparison with recommendations.
    Requires PRO or ENTERPRISE tier.
    """
    from llm_provider import compare_scenarios
    try:
        result = compare_scenarios(
            scenarios=body.scenarios,
            question=body.question,
            model_id=body.model_id,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        return result
    except Exception as exc:
        logger.error("Bedrock compare error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Bedrock model error: {exc}")


class BedrockBatchAnalysisRequest(BaseModel):
    batch_results: List[Dict] = Field(..., min_length=1, max_length=500, description="Link analysis results to analyze")
    question: Optional[str] = Field(None, max_length=4000)
    model_id: Optional[str] = Field(None)
    max_tokens: Optional[int] = Field(None, ge=1, le=4096)
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0)


@app.post("/bedrock/batch-analyze")
async def bedrock_batch_analyze(
    body: BedrockBatchAnalysisRequest,
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """
    Analyze a batch of link analysis results with AI.
    Processes up to 500 link results and provides consolidated
    coverage assessment, worst-link identification, and prioritized
    remediation recommendations.
    Requires PRO or ENTERPRISE tier.
    """
    from llm_provider import analyze_batch
    try:
        result = analyze_batch(
            batch_results=body.batch_results,
            question=body.question,
            model_id=body.model_id,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        return result
    except Exception as exc:
        logger.error("Bedrock batch-analyze error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Bedrock model error: {exc}")


class BedrockAntennaRequest(BaseModel):
    analysis: Dict = Field(..., description="Link analysis result")
    tower: Dict = Field(..., description="Tower information")
    target_clearance: float = Field(0.6, ge=0.0, le=1.0, description="Target Fresnel zone clearance fraction")
    model_id: Optional[str] = Field(None)


@app.post("/bedrock/suggest-height")
async def bedrock_suggest_height(
    body: BedrockAntennaRequest,
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE, Tier.ULTRA)),
):
    """
    AI-powered antenna height recommendation based on link analysis
    and terrain profile. Calculates the optimal height for the desired
    Fresnel zone clearance.
    Requires PRO or ENTERPRISE tier.
    """
    from llm_provider import suggest_antenna_height
    try:
        result = suggest_antenna_height(
            analysis=body.analysis,
            tower=body.tower,
            target_clearance=body.target_clearance,
            model_id=body.model_id,
        )
        return result
    except Exception as exc:
        logger.error("Bedrock suggest-height error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Bedrock model error: {exc}")


# ------------------------------------------------------------
# Run the server (if executed directly)
# ------------------------------------------------------------

# React PWA: serve built frontend (same approach as telecom_tower_power_db)
FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend_dist"

# ── GraphQL router (Strawberry) ───────────────────────────────────────────────
# Mounted at /graphql, side-by-side with REST. Reuses verify_api_key so all
# tier/quota rules apply identically. Optional: GraphiQL UI is gated by
# GRAPHQL_GRAPHIQL=true (off in prod by default to avoid schema disclosure).
try:
    from graphql_schema import get_graphql_router as _build_graphql_router
    _gql_router = _build_graphql_router(verify_api_key, platform)
    if os.getenv("GRAPHQL_GRAPHIQL", "").lower() in ("1", "true", "yes"):
        # Rebuild with GraphiQL on for dev environments.
        from graphql_schema import schema as _gql_schema
        from strawberry.fastapi import GraphQLRouter as _GraphQLRouter
        from fastapi import Depends as _Depends

        async def _ctx(key_data: dict = _Depends(verify_api_key)) -> dict:
            return {
                "key_data": key_data,
                "owner": key_data.get("owner") or "system",
                "tier": key_data.get("tier"), "platform": platform,
            }
        _gql_router = _GraphQLRouter(_gql_schema, context_getter=_ctx, graphql_ide="graphiql")
    app.include_router(_gql_router, prefix="/graphql")
    logger.info("GraphQL router mounted at /graphql (graphiql=%s)",
                os.getenv("GRAPHQL_GRAPHIQL", "false"))
except Exception:
    logger.exception("GraphQL router failed to mount; REST API still active")

# RF engines registry router (itmlogic, signal-server, sionna, ITU-R P.1812).
# Mounted *after* the API-key dependency is in scope so the router inherits
# the same auth posture as the rest of /coverage/*. Failure to import any
# individual engine adapter is non-fatal — see rf_engines/__init__.py.
try:
    from fastapi import Depends as _DependsRF
    from rf_engines_router import router as _rf_engines_router
    app.include_router(
        _rf_engines_router,
        dependencies=[_DependsRF(verify_api_key)],
    )
    logger.info("RF engines router mounted at /coverage/engines")
except Exception:
    logger.exception("RF engines router failed to mount; built-in predictors still active")

if FRONTEND_DIR.is_dir():
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    # Known API path prefixes that must never be served by the SPA
    _API_PREFIXES = (
        "/towers", "/analyze", "/plan_repeater", "/batch_reports",
        "/jobs", "/export_report", "/bedrock", "/srtm",
        "/signup", "/stripe", "/health", "/metrics", "/openapi",
        "/docs", "/redoc", "/graphql",
        "/coverage", "/tenant", "/internal", "/auth",
    )

    @app.middleware("http")
    async def reject_path_traversal(request: Request, call_next):
        """Block requests with path-traversal sequences."""
        from urllib.parse import unquote
        raw = request.scope.get("path", "")
        decoded = unquote(raw)
        if ".." in decoded:
            return Response(
                status_code=400,
                content='{"detail":"Invalid path"}',
                media_type="application/json",
            )
        return await call_next(request)

    @app.middleware("http")
    async def api_prefix_rewrite(request: Request, call_next):
        """Rewrite /api/xxx → /xxx so the React PWA (which uses /api prefix) works."""
        path = request.scope["path"]
        if path.startswith("/api/") or path == "/api":
            request.scope["path"] = path[4:] or "/"
        return await call_next(request)

    class _SPAStaticFiles(StaticFiles):
        """StaticFiles subclass that falls back to index.html for SPA routing,
        but skips API paths so FastAPI routes take precedence."""
        async def get_response(self, path, scope):
            # Never intercept known API paths
            full_path = scope.get("path", f"/{path}")
            if any(full_path.startswith(p) for p in _API_PREFIXES):
                from starlette.responses import Response
                return Response(status_code=404)
            try:
                return await super().get_response(path, scope)
            except Exception:
                return await super().get_response("index.html", scope)

    app.mount("/", _SPAStaticFiles(directory=FRONTEND_DIR, html=True), name="spa")


if __name__ == "__main__":
    import uvicorn  # local import: not available under Lambda runtime
    uvicorn.run(app, host="0.0.0.0", port=8000)
