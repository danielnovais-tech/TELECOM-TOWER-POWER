# worker.py
"""
RQ-based background worker for batch PDF generation.

Works with telecom_tower_power_db.py (async SQLAlchemy API).
Uses synchronous DB access and SRTM-only terrain for worker simplicity.

Run:  rq worker batch_pdfs --url redis://localhost:6379
  or: python worker.py
"""

import csv
import io
import json
import math
import os
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import redis
from rq import Worker, Queue
from sqlalchemy import create_engine, select, text

from pdf_generator import build_pdf_report
from srtm_elevation import SRTMReader
from s3_storage import upload_result

# ------------------------------
# Synchronous DB access
# (RQ workers are sync; we use a sync engine to read the towers table)
# ------------------------------
_ASYNC_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./towers.db")
# Convert async driver URLs to sync equivalents
_SYNC_URL = (
    _ASYNC_URL
    .replace("sqlite+aiosqlite", "sqlite")
    .replace("postgresql+asyncpg", "postgresql+psycopg2")
)
_sync_engine = create_engine(_SYNC_URL, echo=False)

# SRTM reader for terrain profiles
_srtm = SRTMReader(os.getenv("SRTM_DATA_DIR", "./srtm_data"))

# ------------------------------
# Domain models (mirrored from batch_worker.py to avoid importing async API)
# ------------------------------

class Band(str, Enum):
    BAND_700 = "700MHz"
    BAND_1800 = "1800MHz"
    BAND_2600 = "2600MHz"
    BAND_3500 = "3500MHz"

    def to_hz(self) -> float:
        return {
            "700MHz": 700e6, "1800MHz": 1.8e9,
            "2600MHz": 2.6e9, "3500MHz": 3.5e9,
        }[self.value]


@dataclass
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float
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


# ------------------------------
# Link analysis helpers (sync, SRTM-only)
# ------------------------------

class LinkEngine:
    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2) -> float:
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def free_space_path_loss(d_km: float, f_hz: float) -> float:
        return 20 * math.log10(d_km * 1000) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km: float, f_hz: float, d1_km: float, d2_km: float) -> float:
        d1, d2 = d1_km * 1000, d2_km * 1000
        return math.sqrt((299792458 * d1 * d2) / (f_hz * (d1 + d2)))

    @staticmethod
    def terrain_clearance(profile: List[float], d_km: float, f_hz: float,
                          tx_h: float, rx_h: float, k_factor: float = 1.33) -> float:
        """k_factor: effective Earth radius factor (4/3 standard atmosphere)."""
        n = len(profile)
        if n < 2:
            return 1.0
        step = d_km / (n - 1)
        R_eff = 6371.0 * k_factor  # effective Earth radius in km
        min_clear = float("inf")
        for i, ground in enumerate(profile):
            d_i = i * step
            d2 = d_km - d_i
            if d_i <= 0 or d2 <= 0:
                continue
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            # Earth curvature correction: bulge = d1*d2 / (2*R_eff)
            d1_m = d_i * 1000
            d2_m = d2 * 1000
            earth_bulge = (d1_m * d2_m) / (2 * R_eff * 1000)
            clearance = line_h - ground - earth_bulge
            fr = LinkEngine.fresnel_radius(d_km, f_hz, d_i, d2)
            if fr > 0:
                min_clear = min(min_clear, clearance / fr)
        return min_clear if min_clear != float("inf") else 1.0

    @staticmethod
    def estimate_signal(tx_dbm, tx_gain, rx_gain, f_hz, d_km, extra=0.0) -> float:
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_dbm + tx_gain + rx_gain - fspl - extra


def _get_terrain_sync(lat1, lon1, lat2, lon2, num_points=30) -> List[float]:
    heights = []
    for i in range(num_points):
        frac = i / (num_points - 1)
        lat = lat1 + (lat2 - lat1) * frac
        lon = lon1 + (lon2 - lon1) * frac
        elev = _srtm.get_elevation(lat, lon)
        heights.append(elev if elev is not None else 0.0)
    return heights


def _analyze_link(tower: Tower, rx: Receiver, terrain: List[float]) -> LinkResult:
    d_km = LinkEngine.haversine_km(tower.lat, tower.lon, rx.lat, rx.lon)
    f_hz = tower.primary_freq_hz()
    rssi = LinkEngine.estimate_signal(tower.power_dbm, 17.0, rx.antenna_gain_dbi, f_hz, d_km)

    tx_h_asl = rx_h_asl = 0.0
    fresnel_clear = 1.0
    los_ok = True

    if terrain:
        tx_h_asl = terrain[0] + tower.height_m
        rx_h_asl = terrain[-1] + rx.height_m
        fresnel_clear = LinkEngine.terrain_clearance(terrain, d_km, f_hz, tx_h_asl, rx_h_asl)
        los_ok = fresnel_clear > 0.6
        if not los_ok:
            rssi -= (0.6 - fresnel_clear) * 10

    feasible = los_ok and rssi > -95

    if not los_ok:
        rec = (f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). "
               f"Increase receiver height or relocate tower.")
    elif rssi < -95:
        rec = (f"Signal too low ({rssi:.1f} dBm). "
               f"Consider higher gain antenna or repeater at {d_km / 2:.1f} km.")
    else:
        rec = f"Good link. RSSI = {rssi:.1f} dBm. Clear LOS."

    return LinkResult(
        feasible=feasible, signal_dbm=rssi, fresnel_clearance=fresnel_clear,
        los_ok=los_ok, distance_km=d_km, recommendation=rec,
        terrain_profile=terrain or None,
        tx_height_asl=tx_h_asl if terrain else None,
        rx_height_asl=rx_h_asl if terrain else None,
    )


# ------------------------------
# RQ job function
# ------------------------------

def generate_batch_pdfs(tower_id: str, csv_content: str) -> dict:
    """Generate ZIP of PDF reports for all receivers in CSV.

    Called by RQ.  The ZIP is saved to /tmp/{job_id}.zip where job_id
    comes from the RQ job context.
    """
    from rq import get_current_job
    rq_job = get_current_job()
    job_id = rq_job.id if rq_job else tower_id  # fallback for direct calls

    # Fetch tower from DB synchronously
    with _sync_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, lat, lon, height_m, operator, bands, power_dbm "
                 "FROM towers WHERE id = :tid"),
            {"tid": tower_id},
        ).mappings().first()

    if row is None:
        raise ValueError(f"Tower {tower_id} not found")

    tower = Tower(
        id=row["id"], lat=row["lat"], lon=row["lon"],
        height_m=row["height_m"], operator=row["operator"],
        bands=[Band(b) for b in json.loads(row["bands"])],
        power_dbm=row["power_dbm"],
    )
    freq_mhz = tower.primary_freq_hz() / 1e6

    # Parse CSV
    reader = csv.DictReader(io.StringIO(csv_content))
    receivers = []
    for r in reader:
        receivers.append(Receiver(
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            height_m=float(r.get("height", r.get("height_m", 10.0))),
            antenna_gain_dbi=float(r.get("gain", r.get("antenna_gain_dbi", 12.0))),
        ))

    if not receivers:
        raise ValueError("CSV contains no receiver rows")

    # Build ZIP in memory then upload via s3_storage
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, rx in enumerate(receivers):
            terrain = _get_terrain_sync(tower.lat, tower.lon, rx.lat, rx.lon)
            result = _analyze_link(tower, rx, terrain)
            pdf_buf = build_pdf_report(tower, rx, result, terrain, freq_mhz)
            zf.writestr(f"report_{tower_id}_{idx + 1:03d}.pdf", pdf_buf.getvalue())

    location = upload_result(job_id, zip_buffer.getvalue())
    return {"location": location, "count": len(receivers)}


# ------------------------------
# Standalone entry point
# ------------------------------

if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_conn = redis.from_url(redis_url)
    worker = Worker(
        [Queue("batch_pdfs", connection=redis_conn)],
        connection=redis_conn,
    )
    worker.work()
