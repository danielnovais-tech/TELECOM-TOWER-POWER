"""
telecom_tower_power_api.py
TELECOM TOWER POWER - Professional telecom engineering platform
with real terrain elevation (Open-Elevation) and REST API (FastAPI).

Run: uvicorn telecom_tower_power_api:app --reload
"""

import math
import json
import asyncio
import heapq
import io
import secrets
from datetime import datetime, timezone

import aiohttp
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Tuple
from enum import Enum
from fastapi import FastAPI, HTTPException, Query, Depends, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

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
            recommendation=recommendation
        )

    async def plan_repeater_chain(self, start_tower: Tower, target_receiver: Receiver,
                                  max_hops: int = 3,
                                  candidate_sites: Optional[List[Tower]] = None) -> List[Tower]:
        """
        Bottleneck-shortest-path multi-hop repeater optimization.

        Finds the path from start_tower to target_receiver (through candidate
        repeater sites) that minimizes the worst single-hop path loss, subject
        to a max_hops constraint.  This ensures every hop in the chain is
        feasible and the weakest link is as strong as possible.

        If no candidate_sites are given, generates evenly spaced candidates along
        the great-circle path.
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

        # Modified Dijkstra for bottleneck path:
        # cost = worst (max) single-hop FSPL along the path so far
        # We want to MINIMIZE this bottleneck cost.
        # State: (bottleneck_cost, hops, node_id, path)
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
                d_km = LinkEngine.haversine_km(current.lat, current.lon,
                                               neighbor.lat, neighbor.lon)
                if d_km < 0.1:
                    continue
                hop_fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
                hop_rssi = current.power_dbm + tx_gain + rx_gain - hop_fspl
                # Skip hops that would be infeasible (signal below -95 dBm)
                if hop_rssi < -95:
                    continue
                new_bottleneck = max(bottleneck, hop_fspl)
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
    Tier.FREE: {"requests_per_min": 10, "pdf_export": False, "max_towers": 20},
    Tier.PRO: {"requests_per_min": 100, "pdf_export": True, "max_towers": 500},
    Tier.ENTERPRISE: {"requests_per_min": 1000, "pdf_export": True, "max_towers": 10000},
}

# In-memory API key store: key -> {"tier": Tier, "owner": str}
# In production, use a database.
API_KEYS: Dict[str, Dict] = {
    "demo-key-free-001": {"tier": Tier.FREE, "owner": "demo_free"},
    "demo-key-pro-001": {"tier": Tier.PRO, "owner": "demo_pro"},
    "demo-key-enterprise-001": {"tier": Tier.ENTERPRISE, "owner": "demo_enterprise"},
}

api_key_header = APIKeyHeader(name="X-API-Key")

async def verify_api_key(api_key: str = Security(api_key_header)) -> Dict:
    """Validate the API key and return the key metadata (tier, owner)."""
    key_data = API_KEYS.get(api_key)
    if key_data is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
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
# PDF Report Generator
# ------------------------------------------------------------

class PDFReportGenerator:
    @staticmethod
    def generate(tower: Tower, receiver: Receiver, result: LinkResult) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                topMargin=20*mm, bottomMargin=20*mm,
                                leftMargin=15*mm, rightMargin=15*mm)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('Title2', parent=styles['Title'], fontSize=20,
                                     textColor=colors.HexColor('#1a237e'))
        heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                       textColor=colors.HexColor('#283593'))
        elements = []

        # Title
        elements.append(Paragraph("TELECOM TOWER POWER", title_style))
        elements.append(Paragraph("Engineering Link Analysis Report", styles['Heading3']))
        elements.append(Spacer(1, 8*mm))

        # Metadata
        elements.append(Paragraph(
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            styles['Normal']))
        elements.append(Spacer(1, 6*mm))

        table_base_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a237e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ])

        # Tower info table
        elements.append(Paragraph("Tower Information", heading_style))
        tower_data = [
            ["Parameter", "Value"],
            ["ID", tower.id],
            ["Location", f"{tower.lat:.5f}, {tower.lon:.5f}"],
            ["Height (AGL)", f"{tower.height_m:.1f} m"],
            ["Operator", tower.operator],
            ["Bands", ", ".join(b.value if hasattr(b, 'value') else str(b) for b in tower.bands)],
            ["TX Power", f"{tower.power_dbm:.1f} dBm"],
        ]
        t1 = Table(tower_data, colWidths=[55*mm, 110*mm])
        t1.setStyle(table_base_style)
        elements.append(t1)
        elements.append(Spacer(1, 6*mm))

        # Receiver info table
        elements.append(Paragraph("Receiver Information", heading_style))
        rx_data = [
            ["Parameter", "Value"],
            ["Location", f"{receiver.lat:.5f}, {receiver.lon:.5f}"],
            ["Height (AGL)", f"{receiver.height_m:.1f} m"],
            ["Antenna Gain", f"{receiver.antenna_gain_dbi:.1f} dBi"],
        ]
        t2 = Table(rx_data, colWidths=[55*mm, 110*mm])
        t2.setStyle(table_base_style)
        elements.append(t2)
        elements.append(Spacer(1, 6*mm))

        # Analysis results table
        elements.append(Paragraph("Link Analysis Results", heading_style))
        status_color = '#2e7d32' if result.feasible else '#c62828'
        analysis_data = [
            ["Metric", "Value"],
            ["Feasible", "YES" if result.feasible else "NO"],
            ["Distance", f"{result.distance_km:.2f} km"],
            ["Signal (RSSI)", f"{result.signal_dbm:.1f} dBm"],
            ["Fresnel Clearance", f"{result.fresnel_clearance:.3f}"],
            ["Line of Sight", "Clear" if result.los_ok else "Obstructed"],
        ]
        t3 = Table(analysis_data, colWidths=[55*mm, 110*mm])
        t3.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a237e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('TEXTCOLOR', (1, 1), (1, 1), colors.HexColor(status_color)),
            ('FONTNAME', (1, 1), (1, 1), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ]))
        elements.append(t3)
        elements.append(Spacer(1, 6*mm))

        # Recommendation
        elements.append(Paragraph("Recommendation", heading_style))
        elements.append(Paragraph(result.recommendation, styles['Normal']))

        doc.build(elements)
        return buf.getvalue()

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/")
async def root():
    """Health check and API overview."""
    return {
        "service": "TELECOM TOWER POWER API",
        "status": "online",
        "docs": "/docs",
        "endpoints": ["/towers", "/towers/nearest", "/analyze", "/plan_repeater", "/export_report", "/export_report/pdf"],
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
    if len(platform.towers) >= tier_limit:
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

@app.get("/towers")
async def list_towers(operator: Optional[str] = None, limit: int = 100, key_data: Dict = Depends(verify_api_key)):
    """List all towers, optionally filtered by operator."""
    towers_list = list(platform.towers.values())
    if operator:
        towers_list = [t for t in towers_list if t.operator == operator]
    return {"towers": [asdict(t) for t in towers_list[:limit]]}

@app.get("/towers/nearest")
async def nearest_towers(lat: float, lon: float, operator: Optional[str] = None, limit: int = 5, key_data: Dict = Depends(verify_api_key)):
    """Find nearest towers to a given location."""
    nearest = platform.find_nearest_towers(lat, lon, operator, limit)
    return {"nearest_towers": [asdict(t) for t in nearest]}

@app.post("/analyze", response_model=LinkAnalysisResponse)
async def analyze_link(tower_id: str, receiver: ReceiverInput, key_data: Dict = Depends(verify_api_key)):
    """
    Perform point-to-point link analysis between an existing tower and a receiver.
    Automatically fetches real terrain elevation along the path.
    """
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    result = await platform.analyze_link(tower, rx, terrain_profile=None)
    return LinkAnalysisResponse(**asdict(result))

@app.post("/plan_repeater")
async def plan_repeater(tower_id: str, receiver: ReceiverInput, max_hops: int = 3, key_data: Dict = Depends(verify_api_key)):
    """Propose an optimized repeater chain using Dijkstra path search."""
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(**receiver.dict())
    chain = await platform.plan_repeater_chain(tower, rx, max_hops)
    return {"repeater_chain": [asdict(t) for t in chain]}

@app.get("/export_report")
async def export_report(tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(verify_api_key)):
    """Generate a JSON engineering report for a given tower and receiver location."""
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(lat=lat, lon=lon, height_m=height_m, antenna_gain_dbi=antenna_gain)
    result = await platform.analyze_link(tower, rx)
    report = {
        "platform": "TELECOM TOWER POWER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tower": asdict(tower),
        "receiver": asdict(rx),
        "analysis": asdict(result)
    }
    return report

@app.get("/export_report/pdf")
async def export_report_pdf(tower_id: str, lat: float, lon: float, height_m: float = 10.0, antenna_gain: float = 12.0, key_data: Dict = Depends(require_tier(Tier.PRO, Tier.ENTERPRISE))):
    """Generate a professional PDF engineering report (Pro/Enterprise tiers only)."""
    tower = platform.towers.get(tower_id)
    if not tower:
        raise HTTPException(status_code=404, detail=f"Tower {tower_id} not found")
    rx = Receiver(lat=lat, lon=lon, height_m=height_m, antenna_gain_dbi=antenna_gain)
    result = await platform.analyze_link(tower, rx)
    pdf_bytes = PDFReportGenerator.generate(tower, rx, result)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{tower_id}.pdf"}
    )

# ------------------------------------------------------------
# Run the server (if executed directly)
# ------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
