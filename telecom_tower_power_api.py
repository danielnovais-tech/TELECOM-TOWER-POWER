"""
telecom_tower_power_api.py
TELECOM TOWER POWER - Professional telecom engineering platform
with real terrain elevation (Open-Elevation) and REST API (FastAPI).

Run: uvicorn telecom_tower_power_api:app --reload
"""

import math
import json
import asyncio
import aiohttp
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from enum import Enum
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import uvicorn

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
    def __init__(self):
        self.cache: Dict[Tuple[float, float], float] = {}
        self.session = None

    async def _get_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_elevation(self, lat: float, lon: float) -> float:
        """Get elevation in meters. Uses cache first, then Open-Elevation API."""
        key = (round(lat, 5), round(lon, 5))
        if key in self.cache:
            return self.cache[key]

        session = await self._get_session()
        url = f"https://api.open-elevation.com/api/v1/lookup?locations={lat},{lon}"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    elev = data['results'][0]['elevation']
                    self.cache[key] = elev
                    return elev
        except Exception:
            pass
        # fallback: approximate from SRTM? return 0 (sea level) as last resort
        self.cache[key] = 0.0
        return 0.0

    async def get_profile(self, lat1: float, lon1: float, lat2: float, lon2: float,
                          num_points: int = 30) -> List[float]:
        """Return a list of ground heights (m) along the great-circle path."""
        heights = []
        for i in range(num_points):
            frac = i / (num_points - 1)
            lat = lat1 + (lat2 - lat1) * frac
            lon = lon1 + (lon2 - lon1) * frac
            h = await self.get_elevation(lat, lon)
            heights.append(h)
        return heights

    async def close(self):
        if self.session:
            await self.session.close()

# ------------------------------------------------------------
# Main Platform (async-aware)
# ------------------------------------------------------------

class TelecomTowerPower:
    def __init__(self):
        self.towers: Dict[str, Tower] = {}
        self.elevation = ElevationService()

    def add_tower(self, tower: Tower):
        self.towers[tower.id] = tower

    def find_nearest_towers(self, lat: float, lon: float, operator: Optional[str] = None,
                            limit: int = 5) -> List[Tower]:
        distances = []
        for tower in self.towers.values():
            if operator and tower.operator != operator:
                continue
            d = LinkEngine.haversine_km(lat, lon, tower.lat, tower.lon)
            distances.append((d, tower))
        distances.sort(key=lambda x: x[0])
        return [t for _, t in distances[:limit]]

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
            fresnel_clear = LinkEngine.terrain_clearance(
                terrain_profile, d_km, f_hz, tower.height_m, receiver.height_m
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
            recommendation=recommendation
        )

    async def plan_repeater_chain(self, start_tower: Tower, target_receiver: Receiver,
                                  max_hops: int = 3) -> List[Tower]:
        """Simplified greedy: propose one midpoint repeater (MVP)."""
        result = [start_tower]
        mid_lat = (start_tower.lat + target_receiver.lat) / 2
        mid_lon = (start_tower.lon + target_receiver.lon) / 2
        intermediate = Tower(
            id="proposed_repeater_1",
            lat=mid_lat, lon=mid_lon, height_m=40.0,
            operator=start_tower.operator, bands=start_tower.bands, power_dbm=43.0
        )
        result.append(intermediate)
        return result

    async def close(self):
        await self.elevation.close()

# ------------------------------------------------------------
# FastAPI application (Pydantic models for request/response)
# ------------------------------------------------------------

app = FastAPI(title="TELECOM TOWER POWER API", description="Cell tower coverage and repeater planning")

# Global platform instance
platform = TelecomTowerPower()

# Pydantic models for API
class TowerInput(BaseModel):
    id: str
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = Field(..., gt=0)
    operator: str
    bands: List[Band]
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

@app.on_event("startup")
async def startup():
    # Optionally pre-load some towers from a file
    pass

@app.on_event("shutdown")
async def shutdown():
    await platform.close()

@app.post("/towers", status_code=201)
async def add_tower(tower: TowerInput):
    """Add a new tower to the database."""
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

@app.get("/towers")
async def list_towers(operator: Optional[str] = None, limit: int = 100):
    """List all towers, optionally filtered by operator."""
    towers_list = list(platform.towers.values())
    if operator:
        towers_list = [t for t in towers_list if t.operator == operator]
    return {"towers": [asdict(t) for t in towers_list[:limit]]}

@app.get("/towers/nearest")
async def nearest_towers(lat: float, lon: float, operator: Optional[str] = None, limit: int = 5):
    """Find nearest towers to a given location."""
    nearest = platform.find_nearest_towers(lat, lon, operator, limit)
    return {"nearest_towers": [asdict(t) for t in nearest]}

@app.post("/analyze", response_model=LinkAnalysisResponse)
async def analyze_link(tower_id: str, receiver: ReceiverInput):
    """
    Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.
    """
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    result = await platform.analyze_link(tower, rx, terrain_profile=None)  # auto-fetch
    return LinkAnalysisResponse(**asdict(result))

@app.post("/plan_repeater")
async def plan_repeater(tower_id: str, receiver: ReceiverInput, max_hops: int = 3):
    """Propose a repeater tower chain (MVP: single midpoint repeater)."""
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    chain = await platform.plan_repeater_chain(tower, rx, max_hops)
    return {"repeater_chain": [asdict(t) for t in chain]}

@app.get("/export_report")
async def export_report(tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0):
    """Generate a JSON engineering report for a given tower and receiver location."""
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(lat=lat, lon=lon, height_m=height_m, antenna_gain_dbi=antenna_gain)
    result = await platform.analyze_link(tower, rx)
    report = {
        "platform": "TELECOM TOWER POWER",
        "generated_at": "2026-04-04T00:00:00Z",  # would use datetime.utcnow()
        "tower": asdict(tower),
        "receiver": asdict(rx),
        "analysis": asdict(result)
    }
    return report

# ------------------------------------------------------------
# Run the server (if executed directly)
# ------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
