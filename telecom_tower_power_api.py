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
import secrets
import time
import uuid
import zipfile
from datetime import datetime, timezone

import aiohttp
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from enum import Enum
from fastapi import FastAPI, HTTPException, Query, Depends, Security, UploadFile, File, Request
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

# ------------------------------------------------------------
# Core domain models (same as before, with minor enhancements)
# ------------------------------------------------------------

class Band(str, Enum):
    BAND_700 = "700MHz"
    BAND_1800 = "1800MHz"
    BAND_2600 = "2600MHz"
    BAND_3500 = "3500MHz"

    def to_hz(self) -> float:
        return {
            "700MHz": 700e6,
            "1800MHz": 1.8e9,
            "2600MHz": 2.6e9,
            "3500MHz": 3.5e9,
        }[self.value]

@dataclass
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float          # antenna height above ground
    operator: str
    bands: List[Band]
    power_dbm: float = 43.0

    def primary_freq_hz(self) -> float:
        return self.bands[0].to_hz()

@dataclass
class Receiver:
    lat: float
    lon: float
    height_m: float = 10.0
    antenna_gain_dbi: float = 12.0

@dataclass
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
                          tx_h: float, rx_h: float) -> float:
        """
        Returns minimum fraction of first Fresnel zone clearance (0..1+).
        terrain_profile: list of ground heights (m) at equally spaced points.
        """
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n-1)
        min_clearance = float('inf')
        for i, ground_h in enumerate(terrain_profile):
            d_i = i * step
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            clearance = line_h - ground_h
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
    _MAX_RETRIES = 3
    _BASE_DELAY = 1.0          # seconds; doubles on each retry
    _BATCH_TIMEOUT = 60        # seconds for the batch POST
    _SINGLE_TIMEOUT = 30

    def __init__(self, srtm_dir: str | None = None):
        self.cache: Dict[Tuple[float, float], float] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.srtm = SRTMReader(srtm_dir or os.getenv("SRTM_DATA_DIR", "./srtm_data"))

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
                    limit: int = 100) -> List[Tower]:
        rows = self.db.list_all(operator=operator, limit=limit)
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

        # Pre-compute hop costs (FSPL + terrain obstruction penalty)
        # Cache as dict[(from_id, to_id)] -> effective_loss_db
        hop_cost: Dict[Tuple[str, str], float] = {}
        for a in all_nodes:
            for b in all_nodes:
                if a.id == b.id or b.id == start_tower.id:
                    continue
                d_km = LinkEngine.haversine_km(a.lat, a.lon, b.lat, b.lon)
                if d_km < 0.1:
                    hop_cost[(a.id, b.id)] = 0.0
                    continue
                fspl = LinkEngine.free_space_path_loss(d_km, f_hz)

                # Fetch terrain profile for this hop and compute Fresnel clearance
                obstruction_penalty = 0.0
                try:
                    profile = await self.elevation.get_profile(
                        a.lat, a.lon, b.lat, b.lon, num_points=20
                    )
                    if profile:
                        tx_h_asl = profile[0] + a.height_m
                        rx_h_asl = profile[-1] + b.height_m
                        clearance = LinkEngine.terrain_clearance(
                            profile, d_km, f_hz, tx_h_asl, rx_h_asl
                        )
                        if clearance < 0.6:
                            # Penalize obstructed hops: up to 20 dB extra loss
                            obstruction_penalty = (0.6 - clearance) * 33.0
                except Exception:
                    pass  # If terrain fetch fails, use FSPL only

                hop_cost[(a.id, b.id)] = fspl + obstruction_penalty

        # Modified Dijkstra for bottleneck path:
        # cost = worst (max) single-hop effective loss along the path so far
        # We want to MINIMIZE this bottleneck cost.
        INF = float('inf')
        best: Dict[Tuple[str, int], float] = {}
        heap: list = [(0.0, 0, start_tower.id, [start_tower.id])]
        result_path: Optional[List[str]] = None
        result_cost = INF

        while heap:
            bottleneck, hops, nid, path = heapq.heappop(heap)

            if nid == "__target__":
                if bottleneck < result_cost:
                    result_cost = bottleneck
                    result_path = path
                continue

            if hops >= max_hops:
                continue

            state_key = (nid, hops)
            if state_key in best and best[state_key] <= bottleneck:
                continue
            best[state_key] = bottleneck

            current = node_index[nid]
            for neighbor in all_nodes:
                if neighbor.id == nid or neighbor.id == start_tower.id:
                    continue
                edge_key = (nid, neighbor.id)
                if edge_key not in hop_cost:
                    continue
                effective_loss = hop_cost[edge_key]
                hop_rssi = current.power_dbm + tx_gain + rx_gain - effective_loss
                if hop_rssi < -95:
                    continue
                new_bottleneck = max(bottleneck, effective_loss)
                new_state = (neighbor.id, hops + 1)
                if new_state not in best or best[new_state] > new_bottleneck:
                    heapq.heappush(heap, (new_bottleneck, hops + 1, neighbor.id,
                                          path + [neighbor.id]))

        if result_path is None:
            # Fallback: direct path (infeasible, but reported for user awareness)
            return [start_tower]

        # Return tower objects (exclude virtual target)
        return [node_index[nid] for nid in result_path if nid != "__target__"]

    @staticmethod
    def _generate_candidates(start_tower: Tower, target_receiver: Receiver,
                             max_hops: int) -> List[Tower]:
        """Generate candidate repeater sites evenly along the path."""
        num_candidates = max(max_hops * 2, 4)
        candidates = []
        for i in range(1, num_candidates + 1):
            frac = i / (num_candidates + 1)
            lat = start_tower.lat + (target_receiver.lat - start_tower.lat) * frac
            lon = start_tower.lon + (target_receiver.lon - start_tower.lon) * frac
            candidates.append(Tower(
                id=f"candidate_{i}",
                lat=lat, lon=lon, height_m=40.0,
                operator=start_tower.operator, bands=start_tower.bands,
                power_dbm=43.0
            ))
        return candidates

    async def close(self):
        await self.elevation.close()

# ------------------------------------------------------------
# API Key Authentication
# ------------------------------------------------------------

class Tier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"

TIER_LIMITS = {
    Tier.FREE: {"requests_per_min": int(os.getenv("RATE_LIMIT_FREE", "10")), "pdf_export": False, "max_towers": 20},
    Tier.PRO: {"requests_per_min": int(os.getenv("RATE_LIMIT_PRO", "100")), "pdf_export": True, "max_towers": 500},
    Tier.ENTERPRISE: {"requests_per_min": int(os.getenv("RATE_LIMIT_ENTERPRISE", "1000")), "pdf_export": True, "max_towers": 10000},
}

# In-memory API key store: key -> {"tier": Tier, "owner": str}
# Override with VALID_API_KEYS env var: JSON dict {"key": "tier", ...}
# e.g. VALID_API_KEYS='{"prod-key-001":"pro","prod-key-002":"enterprise"}'
_default_api_keys: Dict[str, Dict] = {
    "demo-key-free-001": {"tier": Tier.FREE, "owner": "demo_free"},
    "demo-key-pro-001": {"tier": Tier.PRO, "owner": "demo_pro"},
    "demo-key-enterprise-001": {"tier": Tier.ENTERPRISE, "owner": "demo_enterprise"},
}

_env_keys_raw = os.getenv("VALID_API_KEYS")
if _env_keys_raw:
    _env_keys = json.loads(_env_keys_raw)
    API_KEYS: Dict[str, Dict] = {
        k: {"tier": Tier(v), "owner": k} for k, v in _env_keys.items()
    }
else:
    API_KEYS: Dict[str, Dict] = _default_api_keys

api_key_header = APIKeyHeader(name="X-API-Key")

# ---- Sliding-window rate limiter (per API key) ----
_rate_buckets: Dict[str, collections.deque] = {}

def _check_rate_limit(api_key: str, tier: Tier) -> Tuple[int, int]:
    """Raise 429 if the caller exceeds their tier's requests_per_min.
    Returns (remaining, limit) for response headers."""
    limit = TIER_LIMITS[tier]["requests_per_min"]
    now = time.monotonic()
    window = 60.0  # seconds

    bucket = _rate_buckets.setdefault(api_key, collections.deque())
    # Evict timestamps older than the window
    while bucket and bucket[0] <= now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        RATE_LIMIT_HITS.labels(tier=tier.value).inc()
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
            key_data = {"tier": Tier(dynamic["tier"]), "owner": dynamic["owner"]}
        else:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
    remaining, limit = _check_rate_limit(api_key, key_data["tier"])
    request.state.tier = key_data["tier"].value
    request.state.rate_limit_remaining = remaining
    request.state.rate_limit_limit = limit
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
MAX_BATCH_ROWS = int(os.getenv("MAX_BATCH_ROWS", "100"))

_allowed_origins_raw = os.getenv(
    "CORS_ORIGINS",
    "https://app.telecomtowerpower.com",
)
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]

# ------------------------------------------------------------
# FastAPI application
# ------------------------------------------------------------

app = FastAPI(
    title="TELECOM TOWER POWER API",
    description="Cell tower coverage, link analysis, and repeater planning. "
                "Requires an API key via the `X-API-Key` header.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    expose_headers=["X-RateLimit-Remaining", "X-RateLimit-Limit"],
)

# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    response.headers["Cache-Control"] = "no-store"
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
    return response

# Global platform instance
platform = TelecomTowerPower()
job_store = JobStore()

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
            "/analyze", "/plan_repeater",
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

@app.on_event("startup")
async def startup():
    # Optionally pre-load some towers from a file
    pass

@app.on_event("shutdown")
async def shutdown():
    await platform.close()

@app.post("/towers", status_code=201)
async def add_tower(tower: TowerInput, key_data: Dict = Depends(verify_api_key)):
    """Add a new tower to the database."""
    tier_limit = TIER_LIMITS[key_data["tier"]]["max_towers"]
    if platform.tower_count() >= tier_limit:
        raise HTTPException(status_code=403, detail=f"Tower limit reached for {key_data['tier'].value} tier ({tier_limit})")
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
    return {"message": f"Tower {tower.id} added"}

@app.get("/towers/nearest")
async def nearest_towers(lat: float, lon: float, operator: Optional[str] = None, limit: int = 5, key_data: Dict = Depends(verify_api_key)):
    """Find nearest towers to a given location."""
    nearest = platform.find_nearest_towers(lat, lon, operator, limit)
    return {"nearest_towers": [asdict(t) for t in nearest]}

@app.get("/towers/{tower_id}")
async def get_tower(tower_id: str, key_data: Dict = Depends(verify_api_key)):
    """Get a single tower by ID."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    return asdict(tower)

@app.get("/towers")
async def list_towers(operator: Optional[str] = None, limit: int = 100, key_data: Dict = Depends(verify_api_key)):
    """List all towers, optionally filtered by operator."""
    towers_list = platform.list_towers(operator=operator, limit=limit)
    return {"towers": [asdict(t) for t in towers_list]}

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

@app.post("/plan_repeater")
async def plan_repeater(tower_id: str, receiver: ReceiverInput, max_hops: int = 3, key_data: Dict = Depends(verify_api_key)):
    """Propose an optimized repeater chain using Dijkstra path search."""
    tower = platform.get_tower(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    chain = await platform.plan_repeater_chain(tower, rx, max_hops)
    return {"repeater_chain": [asdict(t) for t in chain]}

@app.get("/export_report")
async def export_report(tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.PRO, Tier.ENTERPRISE))):
    """Generate a professional PDF engineering report (Pro/Enterprise tiers only)."""
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
async def export_report_pdf(tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.PRO, Tier.ENTERPRISE))):
    """Generate a professional PDF engineering report (Pro/Enterprise tiers only)."""
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
    tower_id: str,
    csv_file: UploadFile = File(...),
    receiver_height_m: float = 10.0,
    antenna_gain_dbi: float = 12.0,
    key_data: Dict = Depends(require_tier(Tier.PRO, Tier.ENTERPRISE)),
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

    if len(receivers) > MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"CSV has {len(receivers)} rows, exceeding the maximum of "
                   f"{MAX_BATCH_ROWS}. Reduce the file size and retry.",
        )

    SYNC_BATCH_LIMIT = 100
    if len(receivers) > SYNC_BATCH_LIMIT:
        # Persist job to the database queue for the background worker
        job_id = str(uuid.uuid4())
        receivers_json = json.dumps([
            {"lat": rx.lat, "lon": rx.lon,
             "height_m": rx.height_m, "antenna_gain_dbi": rx.antenna_gain_dbi}
            for rx in receivers
        ])
        job_store.create_job(
            job_id=job_id,
            tower_id=tower_id,
            receivers_json=receivers_json,
            total=len(receivers),
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
async def get_job_status(job_id: str):
    """Poll the status of a background batch job."""
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


@app.get("/jobs/{job_id}/download")
async def download_job_result(job_id: str):
    """Download the ZIP file produced by a completed batch job."""
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job is {job['status']}; cannot download yet",
        )
    result_path = job.get("result_path")
    if not result_path or not os.path.exists(result_path):
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
    tier: str = Field(..., pattern="^(pro|enterprise)$")

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
    """
    try:
        url = stripe_billing.create_checkout_session(body.email, body.tier)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"checkout_url": url}

@app.post("/stripe/webhook")
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

@app.post("/signup/status")
async def signup_status(body: KeyLookupRequest):
    """
    Look up an existing API key by email address.
    Returns the key, tier, and account status.
    """
    info = stripe_billing.get_key_info_for_email(body.email)
    if info is None:
        raise HTTPException(status_code=404, detail="No account found for this email")
    return {
        "api_key": info["api_key"],
        "tier": info["tier"],
        "email": info["email"],
        "has_subscription": info.get("stripe_subscription_id") is not None,
    }

# ------------------------------------------------------------
# Run the server (if executed directly)
# ------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
