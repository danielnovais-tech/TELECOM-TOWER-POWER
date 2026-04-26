"""
telecom_tower_power_api.py
TELECOM TOWER POWER - Professional telecom engineering platform
with real terrain elevation (Open-Elevation) and REST API (FastAPI).

Run: uvicorn telecom_tower_power_api:app --reload
"""

import collections
import csv
import io
import logging
import math
import json
import asyncio
import heapq
import os
import pathlib
import secrets
import time
import uuid
import zipfile
from datetime import datetime, timezone

import aiohttp
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple, Any
from enum import Enum
from fastapi import FastAPI, HTTPException, Query, Depends, Security, UploadFile, File, Request, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
from pdf_generator import build_pdf_report
from srtm_elevation import SRTMReader
import stripe_billing
from tower_db import TowerStore
from job_store import JobStore, JOB_RESULTS_DIR
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
        )

    @staticmethod
    def _tower_to_row(tower: Tower) -> dict:
        return {
            "id": tower.id, "lat": tower.lat, "lon": tower.lon,
            "height_m": tower.height_m, "operator": tower.operator,
            "bands": [b.value for b in tower.bands],
            "power_dbm": tower.power_dbm,
        }

    # ── CRUD (all go through DB) ─────────────────────────────────

    def add_tower(self, tower: Tower):
        self.db.upsert(self._tower_to_row(tower))

    def update_tower(self, tower: Tower):
        self.db.upsert(self._tower_to_row(tower))

    def remove_tower(self, tower_id: str) -> bool:
        return self.db.delete(tower_id)

    def get_tower(self, tower_id: str) -> Optional[Tower]:
        row = self.db.get(tower_id)
        if row is None:
            return None
        return self._row_to_tower(row)

    def list_towers(self, operator: Optional[str] = None,
                    limit: int = 50000, offset: int = 0) -> List[Tower]:
        rows = self.db.list_all(operator=operator, limit=limit, offset=offset)
        return [self._row_to_tower(r) for r in rows]

    def tower_count(self) -> int:
        return self.db.count()

    def find_nearest_towers(self, lat: float, lon: float,
                            operator: Optional[str] = None,
                            limit: int = 5) -> List[Tower]:
        rows = self.db.find_nearest(lat, lon, operator=operator, limit=limit)
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
                rssi -= (0.6 - fresnel_clear) * 10

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

TIER_LIMITS = {
    Tier.FREE: {"requests_per_min": int(os.getenv("RATE_LIMIT_FREE", "10")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_FREE", "5")), "max_towers": 20, "max_batch_rows": 0},
    Tier.STARTER: {"requests_per_min": int(os.getenv("RATE_LIMIT_STARTER", "30")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_STARTER", "50")), "max_towers": 100, "max_batch_rows": 100},
    Tier.PRO: {"requests_per_min": int(os.getenv("RATE_LIMIT_PRO", "100")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_PRO", "500")), "max_towers": 500, "max_batch_rows": 2000},
    Tier.BUSINESS: {"requests_per_min": int(os.getenv("RATE_LIMIT_BUSINESS", "300")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_BUSINESS", "5000")), "max_towers": 2000, "max_batch_rows": 5000},
    Tier.ENTERPRISE: {"requests_per_min": int(os.getenv("RATE_LIMIT_ENTERPRISE", "1000")), "pdf_export": True, "pdf_per_month": int(os.getenv("PDF_QUOTA_ENTERPRISE", "100000")), "max_towers": 10000, "max_batch_rows": 10000},
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

api_key_header = APIKeyHeader(name="X-API-Key")

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
    Returns (remaining, limit) for response headers."""
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

async def verify_api_key(request: Request, api_key: str = Security(api_key_header)) -> Dict:
    """Validate the API key, enforce rate limit, and return key metadata."""
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
    return key_data

def require_tier(*allowed: Tier):
    """Dependency that checks the caller's tier against allowed tiers."""
    async def _check(key_data: Dict = Depends(verify_api_key)):
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

_DEFAULT_ORIGINS = (
    "https://www.telecomtowerpower.com.br,"
    "https://telecomtowerpower.com.br,"
    "https://app.telecomtowerpower.com.br,"
    "https://api.telecomtowerpower.com.br,"
    "https://app.telecomtowerpower.com,"
    "https://dashboard.telecomtowerpower.com,"
    "https://frontend-production-3542d.up.railway.app,"
    "http://localhost:3000,"
    "http://localhost:8000"
)
_allowed_origins_raw = os.getenv("CORS_ORIGINS", _DEFAULT_ORIGINS)
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]

# ------------------------------------------------------------
# FastAPI application
# ------------------------------------------------------------

app = FastAPI(
    title="TELECOM TOWER POWER API",
    description="Cell tower coverage, link analysis, and repeater planning. "
                "Requires an API key via the `X-API-Key` header.",
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
_sqs_client = None
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3
        _sqs_client = boto3.client("sqs")
    return _sqs_client

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


@app.on_event("startup")
async def startup():
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

@app.on_event("shutdown")
async def shutdown():
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

@app.post("/towers", status_code=201)
async def add_tower(tower: TowerInput, key_data: Dict = Depends(verify_api_key)):
    """Add a new tower to the database."""
    tier_limit = TIER_LIMITS[key_data["tier"]]["max_towers"]
    api_key = key_data.get("owner", "unknown")
    created = _towers_created_per_key.get(api_key, 0)
    if created >= tier_limit:
        raise HTTPException(status_code=403, detail=f"Tower creation limit reached for {key_data['tier'].value} tier ({tier_limit})")
    new_tower = Tower(
        id=tower.id,
        lat=tower.lat,
        lon=tower.lon,
        height_m=tower.height_m,
        operator=tower.operator,
        bands=tower.bands,
        power_dbm=tower.power_dbm
    )
    platform.add_tower(new_tower)
    _towers_created_per_key[api_key] = created + 1
    return {"message": f"Tower {tower.id} added"}

@app.get("/towers/nearest")
async def nearest_towers(lat: float, lon: float, operator: Optional[str] = None, limit: int = 5, key_data: Dict = Depends(verify_api_key)):
    """Find nearest towers to a given location."""
    nearest = platform.find_nearest_towers(lat, lon, operator, limit)
    results = []
    for t in nearest:
        d = asdict(t)
        d["distance_km"] = round(LinkEngine.haversine_km(lat, lon, t.lat, t.lon), 3)
        results.append(d)
    return {"nearest_towers": results}

@app.get("/towers/{tower_id}")
async def get_tower(tower_id: str, key_data: Dict = Depends(verify_api_key)):
    """Get a single tower by ID."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    return asdict(tower)

@app.get("/towers")
async def list_towers(
    operator: Optional[str] = None,
    limit: int = Query(default=1000, ge=1, le=50000),
    offset: int = Query(default=0, ge=0),
    key_data: Dict = Depends(verify_api_key),
):
    """List towers with pagination. Use *offset* and *limit* to page through results."""
    towers_list = platform.list_towers(operator=operator, limit=limit, offset=offset)
    total = platform.tower_count()
    return {"towers": [asdict(t) for t in towers_list], "total": total, "offset": offset, "limit": limit}

@app.put("/towers/{tower_id}")
async def update_tower(tower_id: str, tower: TowerInput, key_data: Dict = Depends(verify_api_key)):
    """Update an existing tower.  The tower ID in the path must match the body."""
    if tower.id != tower_id:
        raise HTTPException(status_code=400, detail="Tower ID in path and body must match")
    if platform.get_tower(tower_id) is None:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    updated = Tower(
        id=tower.id, lat=tower.lat, lon=tower.lon,
        height_m=tower.height_m, operator=tower.operator,
        bands=tower.bands, power_dbm=tower.power_dbm,
    )
    platform.update_tower(updated)
    return {"message": f"Tower {tower_id} updated"}

@app.delete("/towers/{tower_id}")
async def delete_tower(tower_id: str, key_data: Dict = Depends(verify_api_key)):
    """Delete a tower from the database."""
    if not platform.remove_tower(tower_id):
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
    rx = Receiver(**receiver.dict())
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
    grid_size: int = Field(default=20, ge=2, le=100)

    feasibility_threshold_dbm: float = -95.0
    explain: bool = False


@app.post("/coverage/predict")
async def coverage_predict(
    body: CoveragePredictRequest,
    _key: Dict = Depends(require_tier(Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
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

    Restricted to Pro / Business / Enterprise tiers.
    """
    # Lazy import to keep cold-start cost off endpoints that don't use ML.
    import coverage_predict as _cp

    # ── Resolve transmitter ────────────────────────────────────────
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
        return {
            "mode": "grid",
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
    )

    response: Dict[str, Any] = {
        "mode": "point",
        "tx": {"lat": tx_lat, "lon": tx_lon, "height_m": tx_h, "power_dbm": tx_power, "freq_hz": f_hz},
        "rx": {"lat": body.rx_lat, "lon": body.rx_lon, "height_m": body.rx_height_m},
        "distance_km": round(d_km, 3),
        "signal_dbm": result.signal_dbm,
        "feasible": result.feasible,
        "confidence": result.confidence,
        "model_source": result.source,
        "model_version": result.model_version,
        "features": result.features,
    }
    if body.explain:
        response["explanation"] = _cp.explain(response)
    return response


@app.post("/plan_repeater")
async def plan_repeater(tower_id: str, receiver: ReceiverInput, max_hops: int = 3, key_data: Dict = Depends(verify_api_key)):
    """Propose an optimized repeater chain using Dijkstra path search."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    chain = await platform.plan_repeater_chain(tower, rx, max_hops)
    return {"repeater_chain": [asdict(t) for t in chain]}


# ---------------------------------------------------------------------------
# Async variant — submit a plan_repeater job and poll for the result.
# Intended for very large candidate sets (max_hops >= 4) where per-edge
# terrain fetches may still exceed HTTP timeouts even after asyncio.gather.
# ---------------------------------------------------------------------------
_REPEATER_JOBS: Dict[str, Dict[str, Any]] = {}
_REPEATER_JOBS_LOCK = asyncio.Lock()
_REPEATER_JOBS_TTL_S = int(os.getenv("REPEATER_JOBS_TTL_S", "900"))  # 15 min
_REPEATER_JOBS_MAX = int(os.getenv("REPEATER_JOBS_MAX", "256"))

async def _reap_repeater_jobs() -> None:
    """Drop completed/failed repeater jobs older than TTL."""
    now = time.time()
    async with _REPEATER_JOBS_LOCK:
        stale = [
            jid for jid, j in _REPEATER_JOBS.items()
            if j["status"] in ("done", "error")
            and (now - j.get("finished_at", now)) > _REPEATER_JOBS_TTL_S
        ]
        for jid in stale:
            _REPEATER_JOBS.pop(jid, None)
        # Hard cap: drop oldest if we're over budget
        if len(_REPEATER_JOBS) > _REPEATER_JOBS_MAX:
            oldest = sorted(
                _REPEATER_JOBS.items(),
                key=lambda kv: kv[1].get("created_at", 0),
            )[: len(_REPEATER_JOBS) - _REPEATER_JOBS_MAX]
            for jid, _ in oldest:
                _REPEATER_JOBS.pop(jid, None)

async def _run_repeater_job(job_id: str, tower: Tower, rx: Receiver, max_hops: int) -> None:
    try:
        chain = await platform.plan_repeater_chain(tower, rx, max_hops)
        async with _REPEATER_JOBS_LOCK:
            _REPEATER_JOBS[job_id].update(
                status="done",
                finished_at=time.time(),
                result={"repeater_chain": [asdict(t) for t in chain]},
            )
    except Exception as exc:  # noqa: BLE001 – surface in job state
        logger.exception("repeater job %s failed", job_id)
        async with _REPEATER_JOBS_LOCK:
            _REPEATER_JOBS[job_id].update(
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
    rx = Receiver(**receiver.dict())

    await _reap_repeater_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    async with _REPEATER_JOBS_LOCK:
        _REPEATER_JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "created_at": now,
            "tower_id": tower_id,
            "max_hops": max_hops,
            # OWASP A01 (IDOR) – lock job to caller's API-key owner so other
            # tenants can't read each other's repeater chains by guessing job_id.
            "owner": key_data.get("owner"),
        }
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
    async with _REPEATER_JOBS_LOCK:
        job = _REPEATER_JOBS.get(job_id)
        # OWASP A01 (IDOR) – return 404 (not 403) when the job belongs to a
        # different owner so we don't leak existence of other tenants' jobs.
        if job is None or (job.get("owner") and job.get("owner") != key_data.get("owner")):
            raise HTTPException(status_code=404, detail="job not found or expired")
        return dict(job)

@app.get("/export_report")
async def export_report(request: Request, tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.FREE, Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE))):
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
async def export_report_pdf(request: Request, tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.FREE, Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE))):
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
    key_data: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
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

        # Publish to SQS if configured (serverless Lambda worker path)
        if SQS_QUEUE_URL:
            _get_sqs().send_message(
                QueueUrl=SQS_QUEUE_URL,
                MessageBody=json.dumps({
                    "job_id": job_id,
                    "tower_id": tower_id,
                    "tier": key_data["tier"].value,
                }),
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
async def get_job_status(job_id: str, _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE))):
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
async def download_job_result(job_id: str, _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE))):
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

class CheckoutRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    tier: str = Field(..., pattern="^(starter|pro|business|enterprise)$")
    billing_cycle: str = Field("monthly", pattern="^(monthly|annual)$")
    country: Optional[str] = Field(
        None,
        min_length=2, max_length=2,
        description="ISO 3166-1 alpha-2 country code for SRTM tile pre-download (enterprise only)",
    )

@app.post("/signup/free", status_code=201)
async def signup_free(body: SignupRequest):
    """Register a free-tier account and receive an API key instantly."""
    try:
        result = stripe_billing.register_free_user(body.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "api_key": result["api_key"],
        "tier": result["tier"],
        "email": result["email"],
        "message": "Free account created. Include your API key in the X-API-Key header.",
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
    _key: Dict = Depends(require_tier(Tier.ENTERPRISE)),
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
    _key: Dict = Depends(require_tier(Tier.ENTERPRISE)),
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
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
):
    """
    Send a prompt to an Amazon Bedrock base foundation model and return
    the generated response.  Supports Titan, Claude, and Llama model families.
    Requires PRO or ENTERPRISE tier.
    """
    from bedrock_service import invoke_model
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
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
):
    """List available Bedrock foundation models for the AI playground."""
    from bedrock_service import list_available_models
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
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
):
    """
    Compare multiple RF scenarios using AI analysis.
    Accepts 2-10 scenarios (e.g. different frequencies, antenna heights)
    and returns an engineering comparison with recommendations.
    Requires PRO or ENTERPRISE tier.
    """
    from bedrock_service import compare_scenarios
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
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
):
    """
    Analyze a batch of link analysis results with AI.
    Processes up to 500 link results and provides consolidated
    coverage assessment, worst-link identification, and prioritized
    remediation recommendations.
    Requires PRO or ENTERPRISE tier.
    """
    from bedrock_service import analyze_batch
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
    _key: Dict = Depends(require_tier(Tier.STARTER, Tier.PRO, Tier.BUSINESS, Tier.ENTERPRISE)),
):
    """
    AI-powered antenna height recommendation based on link analysis
    and terrain profile. Calculates the optimal height for the desired
    Fresnel zone clearance.
    Requires PRO or ENTERPRISE tier.
    """
    from bedrock_service import suggest_antenna_height
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

if FRONTEND_DIR.is_dir():
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    # Known API path prefixes that must never be served by the SPA
    _API_PREFIXES = (
        "/towers", "/analyze", "/plan_repeater", "/batch_reports",
        "/jobs", "/export_report", "/bedrock", "/srtm",
        "/signup", "/stripe", "/health", "/metrics", "/openapi",
        "/docs", "/redoc",
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
