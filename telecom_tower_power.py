# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
telecom_tower_power.py
Professional telecom engineering platform for cell tower coverage analysis.
Focus: rural areas, fixed wireless access (FWA), repeater tower planning.
Author: Based on the TELECOM TOWER POWER narrative (formerly Mapa das Torres).
"""

import heapq
import math
import json
import struct
import os
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Data models
# ------------------------------------------------------------

class Band(Enum):
    BAND_700 = 700e6   # 700 MHz - good range
    BAND_1800 = 1.8e9  # 1800 MHz
    BAND_2600 = 2.6e9  # 2600 MHz
    BAND_3500 = 3.5e9  # 3.5 GHz - 5G

@dataclass(frozen=True)
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float          # antenna height above ground
    operator: str            # Vivo, Claro, TIM, etc.
    bands: List[Band]
    power_dbm: float = 43.0  # typical eirp per band (dBm)
    frequency_mhz: float = None  # auto-set from primary band
    owner: str = "system"    # OWASP A01: tenant scope. "system" = legacy/import.

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
        if self.frequency_mhz is None and self.bands:
            object.__setattr__(self, 'frequency_mhz', self.bands[0].value / 1e6)

@dataclass(frozen=True)
class Receiver:
    lat: float
    lon: float
    height_m: float = 10.0    # typical antenna mast height for rural
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
    signal_dbm: float         # received signal strength
    fresnel_clearance: float  # fraction of first Fresnel zone cleared
    los_ok: bool
    distance_km: float
    recommendation: str

# ------------------------------------------------------------
# Propagation & Link Budget Engine
# ------------------------------------------------------------

class LinkEngine:
    """Calculate point-to-point link budget using ITU-R P.526 (LOS) and free-space + terrain."""

    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    @staticmethod
    def free_space_path_loss(d_km, f_hz):
        """FSPL in dB."""
        d_m = d_km * 1000
        return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km, f_hz, d1_km, d2_km):
        """First Fresnel zone radius (meters) at a point along the path."""
        d = d_km * 1000
        d1 = d1_km * 1000
        d2 = d2_km * 1000
        return math.sqrt((299792458 * d1 * d2) / (f_hz * (d1 + d2)))  # r1 = sqrt(c*d1*d2 / (f*(d1+d2)))

    @staticmethod
    def terrain_clearance(terrain_profile: List[float], d_km: float, f_hz: float,
                          tx_h: float, rx_h: float, k_factor: float = 1.33) -> float:
        """
        Simplified terrain clearance check.
        Returns minimum fraction of first Fresnel zone clearance (0 to 1+).
        Assumes terrain_profile is list of heights (m) at equally spaced points.
        k_factor: effective Earth radius factor (4/3 standard atmosphere).
        """
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n - 1)
        R_eff = 6371.0 * k_factor  # effective Earth radius in km
        min_clearance = float('inf')
        for i, ground_h in enumerate(terrain_profile):
            d_i = i * step
            # straight line height between tx and rx at distance d_i
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            # Earth curvature correction: bulge = d1*d2 / (2*R_eff)
            d1_m = d_i * 1000
            d2_m = (d_km - d_i) * 1000
            earth_bulge = (d1_m * d2_m) / (2 * R_eff * 1000)
            clearance = line_h - ground_h - earth_bulge
            # compute Fresnel radius at this point
            d1 = d_i
            d2 = d_km - d_i
            if d1 <= 0 or d2 <= 0:
                continue
            fresnel_r = LinkEngine.fresnel_radius(d_km, f_hz, d1, d2)
            if fresnel_r > 0:
                min_clearance = min(min_clearance, clearance / fresnel_r)
        return min_clearance if min_clearance != float('inf') else 1.0

    @staticmethod
    def estimate_signal(tx_power_dbm: float, tx_gain_dbi: float, rx_gain_dbi: float,
                        f_hz: float, d_km: float, extra_loss_db: float = 0.0) -> float:
        """Received signal power in dBm."""
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl - extra_loss_db

# ------------------------------------------------------------
# Terrain Elevation Service
# ------------------------------------------------------------

class TerrainService:
    """
    Fetch real ground-elevation profiles for point-to-point links.

    Sources (tried in order):
      1. In-process memory cache (per-key bilinear-rounded elevation)
      2. Redis L2 cache (optional; shared across workers/pods)
      3. Local SRTM .hgt tiles  (fastest on disk, offline)
      4. Open-Elevation REST API (free, no API key)

    Usage:
        ts = TerrainService(srtm_dir="./srtm_tiles")
        profile = ts.profile(lat1, lon1, lat2, lon2, num_points=50)
        # profile -> list of elevation floats (metres AMSL)

    Redis caching is enabled automatically when either ``redis_url`` is
    passed or the ``SRTM_REDIS_URL`` / ``REDIS_URL`` env vars are set.
    """

    # ---- Constants -----------------------------------------------------
    OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
    SRTM1_SAMPLES = 3601   # 1-arc-second tiles (3601 x 3601)
    SRTM3_SAMPLES = 1201   # 3-arc-second tiles (1201 x 1201)
    DEFAULT_SRTM_DIR = os.getenv("SRTM_DATA_DIR", "./srtm_data")
    DEFAULT_TIMEOUT_S = float(os.getenv("TERRAIN_API_TIMEOUT_S", "10"))
    REDIS_KEY_PREFIX = "ttp:terrain:v1"   # version segment allows cache invalidation
    REDIS_TTL_SECONDS = int(os.getenv("TERRAIN_REDIS_TTL", "604800"))  # 7 days
    COORD_ROUND_DIGITS = 5                 # ~1.1 m precision at equator

    def __init__(self, srtm_dir: Optional[str] = None, api_url: Optional[str] = None,
                 timeout_s: Optional[float] = None,
                 redis_url: Optional[str] = None):
        self.srtm_dir = Path(srtm_dir or self.DEFAULT_SRTM_DIR)
        if not self.srtm_dir.exists():
            # Keep behaviour backward-compatible: no dir => SRTM disabled
            self.srtm_dir = None
        self.api_url = api_url or self.OPEN_ELEVATION_URL
        self.timeout_s = timeout_s if timeout_s is not None else self.DEFAULT_TIMEOUT_S
        self._hgt_cache: Dict[str, Optional[bytes]] = {}
        self._mem_cache: Dict[Tuple[float, float], float] = {}

        # Optional Redis L2 cache
        _url = redis_url or os.getenv("SRTM_REDIS_URL") or os.getenv("REDIS_URL")
        self._redis = None
        if _url:
            try:
                import redis as _redis_mod  # type: ignore[import-not-found]
                self._redis = _redis_mod.Redis.from_url(_url, decode_responses=True)
                self._redis.ping()
                logger.info("TerrainService Redis cache enabled at %s", _url)
            except Exception as exc:  # noqa: BLE001 – cache is best-effort
                logger.warning("TerrainService Redis disabled (%s); using memory+disk only", exc)
                self._redis = None

    # -- path interpolation --------------------------------------------------

    @staticmethod
    def interpolate_path(lat1: float, lon1: float,
                         lat2: float, lon2: float,
                         num_points: int = 50) -> List[Tuple[float, float]]:
        """Return *num_points* equally-spaced (lat, lon) along the great-circle arc."""
        phi1, lam1 = math.radians(lat1), math.radians(lon1)
        phi2, lam2 = math.radians(lat2), math.radians(lon2)

        d = 2 * math.asin(math.sqrt(
            math.sin((phi2 - phi1) / 2) ** 2 +
            math.cos(phi1) * math.cos(phi2) * math.sin((lam2 - lam1) / 2) ** 2
        ))
        if d < 1e-12:
            return [(lat1, lon1)] * num_points

        points = []
        for i in range(num_points):
            f = i / (num_points - 1)
            a = math.sin((1 - f) * d) / math.sin(d)
            b = math.sin(f * d) / math.sin(d)
            x = a * math.cos(phi1) * math.cos(lam1) + b * math.cos(phi2) * math.cos(lam2)
            y = a * math.cos(phi1) * math.sin(lam1) + b * math.cos(phi2) * math.sin(lam2)
            z = a * math.sin(phi1) + b * math.sin(phi2)
            lat = math.degrees(math.atan2(z, math.sqrt(x ** 2 + y ** 2)))
            lon = math.degrees(math.atan2(y, x))
            points.append((lat, lon))
        return points

    # -- SRTM .hgt reader ----------------------------------------------------

    def _hgt_filename(self, lat: float, lon: float) -> str:
        """E.g. S16W048.hgt for lat=-15.78, lon=-47.93."""
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        return f"{ns}{abs(int(math.floor(lat))):02d}{ew}{abs(int(math.floor(lon))):03d}.hgt"

    def _load_hgt(self, filename: str) -> Optional[bytes]:
        if filename in self._hgt_cache:
            return self._hgt_cache[filename]
        if self.srtm_dir is None:
            return None
        path = self.srtm_dir / filename
        if not path.is_file():
            self._hgt_cache[filename] = None
            return None
        data = path.read_bytes()
        self._hgt_cache[filename] = data
        return data

    def _read_hgt_elevation(self, lat: float, lon: float) -> Optional[float]:
        """Read a single elevation from local SRTM .hgt tile."""
        filename = self._hgt_filename(lat, lon)
        data = self._load_hgt(filename)
        if data is None:
            return None

        size = len(data)
        if size == self.SRTM1_SAMPLES ** 2 * 2:
            samples = self.SRTM1_SAMPLES
        elif size == self.SRTM3_SAMPLES ** 2 * 2:
            samples = self.SRTM3_SAMPLES
        else:
            logger.warning("Unexpected .hgt file size for %s: %d bytes", filename, size)
            return None

        lat_frac = lat - math.floor(lat)
        lon_frac = lon - math.floor(lon)
        row = int(round((1 - lat_frac) * (samples - 1)))
        col = int(round(lon_frac * (samples - 1)))
        row = max(0, min(samples - 1, row))
        col = max(0, min(samples - 1, col))
        offset = (row * samples + col) * 2
        if offset + 2 > size:
            return None
        value = struct.unpack(">h", data[offset:offset + 2])[0]
        if value == -32768:          # SRTM void
            return None
        return float(value)

    def _elevations_from_srtm(self, points: List[Tuple[float, float]]) -> Optional[List[float]]:
        """Try to resolve all points from local SRTM tiles; None if any tile is missing."""
        elevations = []
        for lat, lon in points:
            h = self._read_hgt_elevation(lat, lon)
            if h is None:
                return None          # fall through to API
            elevations.append(h)
        return elevations

    # -- Open-Elevation API ---------------------------------------------------

    def _elevations_from_api(self, points: List[Tuple[float, float]]) -> List[float]:
        """Query Open-Elevation (or compatible) for a batch of lat/lon points."""
        locations = [{"latitude": lat, "longitude": lon} for lat, lon in points]
        payload = json.dumps({"locations": locations}).encode("utf-8")

        req = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise RuntimeError(f"Open-Elevation API request failed: {exc}") from exc

        results = body.get("results")
        if not results or len(results) != len(points):
            raise RuntimeError("Unexpected response from Open-Elevation API")
        return [r["elevation"] for r in results]

    # -- public interface -----------------------------------------------------

    def _cache_key(self, lat: float, lon: float) -> Tuple[float, float]:
        return (round(lat, self.COORD_ROUND_DIGITS), round(lon, self.COORD_ROUND_DIGITS))

    def _redis_key(self, key: Tuple[float, float]) -> str:
        return f"{self.REDIS_KEY_PREFIX}:{key[0]:.5f}:{key[1]:.5f}"

    def _redis_mget(self, keys: List[Tuple[float, float]]) -> List[Optional[float]]:
        if self._redis is None or not keys:
            return [None] * len(keys)
        try:
            raw = self._redis.mget([self._redis_key(k) for k in keys])
        except Exception as exc:  # noqa: BLE001
            logger.warning("TerrainService Redis mget failed: %s", exc)
            return [None] * len(keys)
        out: List[Optional[float]] = []
        for v in raw:
            if v is None:
                out.append(None)
            else:
                try:
                    out.append(float(v))
                except (TypeError, ValueError):
                    out.append(None)
        return out

    def _redis_mset(self, items: Dict[Tuple[float, float], float]) -> None:
        if self._redis is None or not items:
            return
        try:
            pipe = self._redis.pipeline()
            for k, v in items.items():
                pipe.setex(self._redis_key(k), self.REDIS_TTL_SECONDS, f"{v:.3f}")
            pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("TerrainService Redis mset failed: %s", exc)

    def profile(self, lat1: float, lon1: float,
                lat2: float, lon2: float,
                num_points: int = 50) -> List[float]:
        """
        Return a terrain elevation profile (list of heights in metres AMSL)
        along the great-circle from (lat1, lon1) to (lat2, lon2).

        Resolution order: memory cache → Redis L2 (if configured) → local
        SRTM .hgt tiles → Open-Elevation API. Partial hits are honoured,
        i.e. only the still-unknown points fall through to the next layer.
        """
        points = self.interpolate_path(lat1, lon1, lat2, lon2, num_points)
        keys = [self._cache_key(lat, lon) for lat, lon in points]
        elevations: List[Optional[float]] = [None] * num_points

        # 1. Memory cache
        for i, k in enumerate(keys):
            if k in self._mem_cache:
                elevations[i] = self._mem_cache[k]

        # 2. Redis L2 for whatever is still missing
        missing_idx = [i for i, e in enumerate(elevations) if e is None]
        if missing_idx and self._redis is not None:
            redis_vals = self._redis_mget([keys[i] for i in missing_idx])
            for i, v in zip(missing_idx, redis_vals):
                if v is not None:
                    elevations[i] = v
                    self._mem_cache[keys[i]] = v

        # 3. Local SRTM for the remaining gaps
        missing_idx = [i for i, e in enumerate(elevations) if e is None]
        srtm_new: Dict[Tuple[float, float], float] = {}
        for i in list(missing_idx):
            lat, lon = points[i]
            h = self._read_hgt_elevation(lat, lon)
            if h is not None:
                elevations[i] = h
                self._mem_cache[keys[i]] = h
                srtm_new[keys[i]] = h
        if srtm_new:
            logger.info("Terrain profile: %d/%d points from local SRTM",
                        len(srtm_new), num_points)
            self._redis_mset(srtm_new)

        # 4. Open-Elevation API fallback for whatever is still missing
        missing_idx = [i for i, e in enumerate(elevations) if e is None]
        if missing_idx:
            logger.info("TerrainService API fallback for %d/%d points via %s",
                        len(missing_idx), num_points, self.api_url)
            api_pts = [points[i] for i in missing_idx]
            api_vals = self._elevations_from_api(api_pts)
            api_new: Dict[Tuple[float, float], float] = {}
            for i, v in zip(missing_idx, api_vals):
                elevations[i] = v
                self._mem_cache[keys[i]] = v
                api_new[keys[i]] = v
            self._redis_mset(api_new)

        return [float(e) if e is not None else 0.0 for e in elevations]


# ------------------------------------------------------------
# Main Platform Class
# ------------------------------------------------------------

class TelecomTowerPower:
    """Main SaaS platform for cell tower coverage analysis and repeater planning."""

    def __init__(self, srtm_dir: Optional[str] = None, elevation_api_url: Optional[str] = None):
        self.towers: Dict[str, Tower] = {}
        self.terrain = TerrainService(srtm_dir=srtm_dir, api_url=elevation_api_url)

    def add_tower(self, tower: Tower):
        self.towers[tower.id] = tower

    def load_towers_from_geojson(self, filepath: str):
        """Load towers from a GeoJSON file (simulated)."""
        # In real implementation, read GeoJSON with lat/lon and properties.
        pass

    def find_nearest_towers(self, lat: float, lon: float, operator: Optional[str] = None, limit: int = 5) -> List[Tower]:
        """Return nearest towers sorted by distance."""
        distances = []
        for tower in self.towers.values():
            if operator and tower.operator != operator:
                continue
            d = LinkEngine.haversine_km(lat, lon, tower.lat, tower.lon)
            distances.append((d, tower))
        distances.sort(key=lambda x: x[0])
        return [t for _, t in distances[:limit]]

    def analyze_link(self, tower: Tower, receiver: Receiver,
                     terrain_profile: Optional[List[float]] = None,
                     terrain_points: int = 50) -> LinkResult:
        """
        Full point-to-point analysis including LOS, Fresnel clearance, and RSSI.

        If *terrain_profile* is None the platform automatically fetches real
        elevation data via SRTM tiles or the Open-Elevation API.
        """
        d_km = LinkEngine.haversine_km(tower.lat, tower.lon, receiver.lat, receiver.lon)
        f_hz = tower.bands[0].value  # use primary band

        # Estimate received signal (free-space base)
        # Assume tower antenna gain ~17 dBi typical for sectorial, receiver gain given
        tx_gain = 17.0
        rx_gain = receiver.antenna_gain_dbi
        rssi = LinkEngine.estimate_signal(tower.power_dbm, tx_gain, rx_gain, f_hz, d_km)

        # Terrain & LOS check
        los_ok = True
        fresnel_clear = 1.0

        # Auto-fetch terrain if none supplied
        if terrain_profile is None:
            try:
                terrain_profile = self.terrain.profile(
                    tower.lat, tower.lon, receiver.lat, receiver.lon,
                    num_points=terrain_points,
                )
            except RuntimeError as exc:
                logger.warning("Could not fetch terrain: %s — assuming flat ground", exc)

        if terrain_profile:
            # tower.height_m and receiver.height_m are above ground level (AGL).
            # terrain_profile values are metres above mean sea level (AMSL).
            # Convert antenna heights to AMSL for clearance calculation.
            tx_h_amsl = terrain_profile[0] + tower.height_m
            rx_h_amsl = terrain_profile[-1] + receiver.height_m
            fresnel_clear = LinkEngine.terrain_clearance(
                terrain_profile, d_km, f_hz, tx_h_amsl, rx_h_amsl
            )
            los_ok = fresnel_clear > 0.6   # rule of thumb: 60% clearance needed for reliable link
            if fresnel_clear < 0.6:
                # Knife-edge diffraction loss saturates ~40 dB (ITU-R P.526);
                # cap so deep negative clearance can't yield unphysical RSSI.
                rssi -= min((0.6 - fresnel_clear) * 10, 40.0)  # empirical extra loss due to obstruction

        feasible = los_ok and (rssi > -95)   # -95 dBm threshold for 4G/5G reliable

        # Recommendation for repeater tower planning
        if not los_ok:
            recommendation = (
                f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). "
                f"Increase receiver height to > {tower.height_m + 10:.0f}m or move tower."
            )
        elif rssi < -95:
            recommendation = (
                f"Signal too low ({rssi:.1f} dBm). "
                f"Consider higher gain antenna or use a repeater tower at distance {d_km/2:.1f}km."
            )
        else:
            recommendation = f"Good link. RSSI = {rssi:.1f} dBm. Clear LOS."

        return LinkResult(
            feasible=feasible,
            signal_dbm=rssi,
            fresnel_clearance=fresnel_clear,
            los_ok=los_ok,
            distance_km=d_km,
            recommendation=recommendation
        )

    def plan_repeater_chain(self, start_tower: Tower, target_receiver: Receiver,
                            max_hops: int = 3,
                            candidate_sites: Optional[List[Tower]] = None) -> List[Tower]:
        """
        Bottleneck-shortest-path multi-hop repeater optimization with
        terrain-aware LOS scoring.

        Finds the path from start_tower to target_receiver (through candidate
        repeater sites) that minimizes the worst single-hop effective loss,
        subject to a max_hops constraint.  Hops with obstructed Fresnel zones
        receive additional loss penalties so the optimizer prefers clear-LOS
        paths.

        If no candidate_sites are given, generates evenly spaced candidates
        along the great-circle path.
        """
        f_hz = start_tower.bands[0].value
        tx_gain = 17.0
        rx_gain = 12.0

        if candidate_sites is None:
            candidate_sites = self._generate_candidates(start_tower, target_receiver, max_hops)

        target_node = Tower(
            id="__target__",
            lat=target_receiver.lat, lon=target_receiver.lon,
            height_m=target_receiver.height_m,
            operator=start_tower.operator, bands=start_tower.bands, power_dbm=43.0
        )

        all_nodes: List[Tower] = [start_tower] + candidate_sites + [target_node]
        node_index = {t.id: t for t in all_nodes}

        # Max feasible single-hop distance: solve FSPL = Pt + Gt + Gr - (-95)
        max_hop_km = 10 ** ((start_tower.power_dbm + tx_gain + rx_gain + 95 - 20 * math.log10(f_hz) + 147.55) / 20) / 1000

        # Pre-compute hop costs with terrain obstruction penalties.
        # Each pair is memoised in hop_cache (Redis-shared, terrain-stable
        # 30d TTL) so repeat planning calls hit O(1) per edge instead of
        # re-sampling the SRTM profile.
        try:
            from hop_cache import get_or_compute as _hop_get_or_compute, make_key as _hop_make_key  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            _hop_get_or_compute = None  # type: ignore[assignment]
            _hop_make_key = None  # type: ignore[assignment]

        hop_cost: Dict[Tuple[str, str], float] = {}

        def _compute_edge(a: Tower, b: Tower, d_km: float) -> Tuple[float, bool]:
            fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
            # Scale terrain sample count with distance (min 10 points)
            num_terrain_pts = max(10, int(d_km * 2))
            obstruction_penalty = 0.0
            try:
                profile = self.terrain.profile(
                    a.lat, a.lon, b.lat, b.lon, num_points=num_terrain_pts
                )
                if profile:
                    tx_h_asl = profile[0] + a.height_m
                    rx_h_asl = profile[-1] + b.height_m
                    clearance = LinkEngine.terrain_clearance(
                        profile, d_km, f_hz, tx_h_asl, rx_h_asl
                    )
                    if clearance < 0.6:
                        obstruction_penalty = (0.6 - clearance) * 33.0
            except RuntimeError:
                pass
            cost = fspl + obstruction_penalty
            # Feasibility = RSSI threshold at -95 dBm (match Dijkstra prune).
            feasible = (a.power_dbm + tx_gain + rx_gain - cost) >= -95.0
            return cost, feasible

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

                if _hop_get_or_compute is not None and _hop_make_key is not None:
                    key = _hop_make_key(
                        a.lat, a.lon, a.height_m,
                        b.lat, b.lon, b.height_m,
                        f_hz, a.power_dbm,
                    )
                    cost, _feasible = _hop_get_or_compute(
                        key, lambda a=a, b=b, d_km=d_km: _compute_edge(a, b, d_km),
                        tower_ids=(a.id, b.id),
                    )
                else:
                    cost, _feasible = _compute_edge(a, b, d_km)

                hop_cost[(a.id, b.id)] = cost

        # Build adjacency list from pre-computed costs for faster neighbor lookup
        adjacency: Dict[str, List[str]] = {t.id: [] for t in all_nodes}
        for (a_id, b_id) in hop_cost:
            adjacency[a_id].append(b_id)

        # Dijkstra for bottleneck path (predecessor map instead of path-in-heap)
        INF = float('inf')
        best: Dict[Tuple[str, int], float] = {}
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
            return [start_tower]

        # Reconstruct path from predecessor map
        result_path: List[str] = []
        state = result_state
        while state is not None:
            result_path.append(state[0])
            state = predecessor.get(state)
        result_path.reverse()

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

    def export_report(self, link_result: LinkResult, tower: Tower, receiver: Receiver) -> str:
        """Generate a professional PDF-ready report (JSON summary here)."""
        report = {
            "platform": "TELECOM TOWER POWER",
            "tower_id": tower.id,
            "tower_location": {"lat": tower.lat, "lon": tower.lon},
            "receiver_location": {"lat": receiver.lat, "lon": receiver.lon},
            "distance_km": round(link_result.distance_km, 2),
            "signal_dbm": round(link_result.signal_dbm, 1),
            "los_ok": link_result.los_ok,
            "fresnel_clearance_pct": round(link_result.fresnel_clearance * 100, 1),
            "feasible": link_result.feasible,
            "recommendation": link_result.recommendation
        }
        return json.dumps(report, indent=2)

# ------------------------------------------------------------
# Example usage (simulating a rural repeater project)
# ------------------------------------------------------------

if __name__ == "__main__":
    # Point srtm_dir at a folder of .hgt tiles for offline use (optional).
    platform = TelecomTowerPower(srtm_dir=os.environ.get("SRTM_DIR"))

    # Add a real tower (e.g., in rural Brazil)
    tower_agro = Tower(
        id="VIVO_AGRO_001",
        lat=-15.7801, lon=-47.9292,  # near Brasília
        height_m=45.0,
        operator="Vivo",
        bands=[Band.BAND_700, Band.BAND_1800],
        power_dbm=46.0
    )
    platform.add_tower(tower_agro)

    # Receiver at a farm 12 km away
    farm_receiver = Receiver(
        lat=-15.8500, lon=-47.8100,
        height_m=12.0,
        antenna_gain_dbi=15.0
    )

    # Terrain is now fetched automatically (SRTM tiles or Open-Elevation API).
    # Pass terrain_profile=<list> to override with your own data.
    print("Fetching real terrain elevation profile ...")
    result = platform.analyze_link(tower_agro, farm_receiver)

    print("=== TELECOM TOWER POWER - Link Analysis ===")
    print(f"Distance: {result.distance_km:.2f} km")
    print(f"LOS OK: {result.los_ok}")
    print(f"Fresnel clearance: {result.fresnel_clearance*100:.1f}%")
    print(f"Estimated RSSI: {result.signal_dbm:.1f} dBm")
    print(f"Feasible: {result.feasible}")
    print(f"Recommendation: {result.recommendation}")

    # Generate report
    report_json = platform.export_report(result, tower_agro, farm_receiver)
    print("\n--- Engineering Report (JSON) ---")
    print(report_json)

    # Repeater planning suggestion
    print("\n--- Repeater Chain Proposal ---")
    chain = platform.plan_repeater_chain(tower_agro, farm_receiver)
    print(f"Suggested repeater tower at: ({chain[1].lat:.4f}, {chain[1].lon:.4f}), height {chain[1].height_m}m")
