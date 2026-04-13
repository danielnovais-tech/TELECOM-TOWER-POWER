"""
batch_worker.py – Standalone worker that processes batch PDF jobs.

Polls the batch_jobs table for queued work, generates ZIP files of PDF
reports, and saves results to disk.  Run as a separate process/container:

    python batch_worker.py
    python batch_worker.py --poll-interval 2   # check every 2 seconds

The worker shares the same database (SQLite or PostgreSQL) as the API,
so multiple workers can run concurrently for horizontal scaling.
"""

import argparse
import io
import json
import logging
import os
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed

from job_store import JobStore, JOB_RESULTS_DIR
from tower_db import TowerStore
from pdf_generator import build_pdf_report
from srtm_elevation import SRTMReader
from prometheus_client import Histogram, Gauge

# Re-use the same metric names so a shared /metrics scrape sees worker data
BATCH_JOB_DURATION = Histogram(
    "batch_jobs_duration_seconds",
    "Time to process a batch job from start to finish",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
)
BATCH_JOBS_ACTIVE = Gauge(
    "batch_jobs_active",
    "Number of background batch jobs currently running",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [worker] %(levelname)s %(message)s",
)
logger = logging.getLogger("batch_worker")

# Re-use the same domain models from the API (lightweight import)
sys.path.insert(0, os.path.dirname(__file__))


# ── Inline domain helpers (avoid importing the full async API) ────

import math
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum


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


class LinkEngine:
    @staticmethod
    def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def free_space_path_loss(d_km: float, f_hz: float) -> float:
        d_m = d_km * 1000
        return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km: float, f_hz: float, d1_km: float, d2_km: float) -> float:
        d1 = d1_km * 1000
        d2 = d2_km * 1000
        c = 299792458
        return math.sqrt((c * d1 * d2) / (f_hz * (d1 + d2)))

    @staticmethod
    def terrain_clearance(terrain_profile: List[float], d_km: float, f_hz: float,
                          tx_h: float, rx_h: float) -> float:
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n - 1)
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

    @staticmethod
    def estimate_signal(tx_power_dbm: float, tx_gain_dbi: float,
                        rx_gain_dbi: float, f_hz: float, d_km: float,
                        extra_loss_db: float = 0.0) -> float:
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl - extra_loss_db


def _get_terrain_profile_sync(
    srtm: SRTMReader, lat1: float, lon1: float,
    lat2: float, lon2: float, num_points: int = 30,
) -> List[float]:
    """Synchronous terrain profile using local SRTM tiles only."""
    heights: List[float] = []
    for i in range(num_points):
        frac = i / (num_points - 1)
        lat = lat1 + (lat2 - lat1) * frac
        lon = lon1 + (lon2 - lon1) * frac
        elev = srtm.get_elevation(lat, lon)
        heights.append(elev if elev is not None else 0.0)
    return heights


def _analyze_link_sync(
    tower: Tower, receiver: Receiver,
    terrain_profile: List[float],
) -> LinkResult:
    """Synchronous link analysis (no async elevation fetch)."""
    d_km = LinkEngine.haversine_km(tower.lat, tower.lon, receiver.lat, receiver.lon)
    f_hz = tower.primary_freq_hz()
    tx_gain = 17.0
    rx_gain = receiver.antenna_gain_dbi
    rssi = LinkEngine.estimate_signal(tower.power_dbm, tx_gain, rx_gain, f_hz, d_km)

    los_ok = True
    fresnel_clear = 1.0
    tx_h_asl = 0.0
    rx_h_asl = 0.0

    if terrain_profile:
        tx_h_asl = terrain_profile[0] + tower.height_m
        rx_h_asl = terrain_profile[-1] + receiver.height_m
        fresnel_clear = LinkEngine.terrain_clearance(
            terrain_profile, d_km, f_hz, tx_h_asl, rx_h_asl
        )
        los_ok = fresnel_clear > 0.6
        if fresnel_clear < 0.6:
            rssi -= (0.6 - fresnel_clear) * 10

    feasible = los_ok and (rssi > -95)

    if not los_ok:
        recommendation = (
            f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). "
            f"Increase receiver height to > {tower.height_m + 10:.0f}m or move tower."
        )
    elif rssi < -95:
        recommendation = (
            f"Signal too low ({rssi:.1f} dBm). "
            f"Consider higher gain antenna or use a repeater tower at distance {d_km / 2:.1f}km."
        )
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


# ── Job processor ────────────────────────────────────────────────

# Maximum parallel PDF workers (default: CPU count, capped at 8)
_MAX_PDF_WORKERS = min(int(os.getenv("PDF_WORKERS", os.cpu_count() or 4)), 8)


def _generate_single_pdf(args: tuple) -> tuple:
    """Generate one PDF report in a worker process.

    Accepts and returns plain serialisable types so this function can be
    dispatched via ProcessPoolExecutor.

    Returns ``(idx, pdf_bytes)`` on success or ``(idx, None)`` on error.
    """
    (idx, tower_dict, rx_data, srtm_data_dir, freq_mhz) = args

    try:
        srtm = SRTMReader(srtm_data_dir)

        tower = Tower(
            id=tower_dict["id"], lat=tower_dict["lat"], lon=tower_dict["lon"],
            height_m=tower_dict["height_m"], operator=tower_dict["operator"],
            bands=[Band(b) for b in tower_dict["bands"]],
            power_dbm=tower_dict["power_dbm"],
        )
        rx = Receiver(
            lat=rx_data["lat"], lon=rx_data["lon"],
            height_m=rx_data.get("height_m", 10.0),
            antenna_gain_dbi=rx_data.get("antenna_gain_dbi", 12.0),
        )

        terrain = _get_terrain_profile_sync(
            srtm, tower.lat, tower.lon, rx.lat, rx.lon,
        )
        result = _analyze_link_sync(tower, rx, terrain)
        pdf_buf = build_pdf_report(tower, rx, result, terrain, freq_mhz)
        return (idx, pdf_buf.getvalue())
    except Exception:
        logging.getLogger("batch_worker").exception(
            "PDF worker failed for receiver %d", idx,
        )
        return (idx, None)


def process_job(job: dict, tower_store: TowerStore,  # type: ignore[type-arg]
                srtm: SRTMReader, job_store: object) -> None:
    """Generate all PDFs for a single batch job and save result to disk.

    Uses a ProcessPoolExecutor to parallelise CPU-heavy PDF generation
    across multiple cores.
    """
    job_id = job["id"]
    tower_id = job["tower_id"]
    logger.info("Processing job %s  tower=%s  total=%d", job_id, tower_id, job["total"])
    start_time = time.monotonic()
    BATCH_JOBS_ACTIVE.inc()

    # Look up tower from DB
    tower_row = tower_store.get(tower_id)
    if tower_row is None:
        getattr(job_store, "fail_job")(job_id, f"Tower {tower_id} not found in DB")
        return

    tower_dict = {
        "id": tower_row["id"], "lat": tower_row["lat"], "lon": tower_row["lon"],
        "height_m": tower_row["height_m"], "operator": tower_row["operator"],
        "bands": tower_row["bands"],
        "power_dbm": tower_row["power_dbm"],
    }

    receivers_data = json.loads(job["receivers"])
    tower_obj = Tower(
        id=tower_dict["id"], lat=tower_dict["lat"], lon=tower_dict["lon"],
        height_m=tower_dict["height_m"], operator=tower_dict["operator"],
        bands=[Band(b) for b in tower_dict["bands"]],
        power_dbm=tower_dict["power_dbm"],
    )
    freq_mhz = tower_obj.primary_freq_hz() / 1e6
    srtm_data_dir = srtm.data_dir

    result_filename = f"batch_{job_id}.zip"
    result_path = os.path.join(JOB_RESULTS_DIR, result_filename)

    try:
        # Build work items for the process pool
        work_items = [
            (idx, tower_dict, rx_data, srtm_data_dir, freq_mhz)
            for idx, rx_data in enumerate(receivers_data)
        ]

        pdf_results: dict[int, bytes] = {}
        completed = 0
        num_workers = min(_MAX_PDF_WORKERS, len(work_items))

        with ProcessPoolExecutor(max_workers=num_workers) as pool:
            futures = {pool.submit(_generate_single_pdf, item): item[0] for item in work_items}
            for future in as_completed(futures):
                idx, pdf_bytes = future.result()
                if pdf_bytes is not None:
                    pdf_results[idx] = pdf_bytes
                completed += 1
                if completed % 10 == 0 or completed == len(receivers_data):
                    getattr(job_store, "update_progress")(job_id, completed)

        # Write ZIP in original order
        with zipfile.ZipFile(result_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx in range(len(receivers_data)):
                if idx in pdf_results:
                    filename = f"report_{tower_id}_{idx + 1:03d}.pdf"
                    zf.writestr(filename, pdf_results[idx])

        getattr(job_store, "complete_job")(job_id, result_path)
        logger.info("Job %s completed: %d PDFs → %s", job_id, len(receivers_data), result_path)

    except Exception as exc:
        getattr(job_store, "fail_job")(job_id, str(exc))
        logger.exception("Job %s failed", job_id)
        # Clean up partial file
        if os.path.exists(result_path):
            os.remove(result_path)
    finally:
        BATCH_JOB_DURATION.observe(time.monotonic() - start_time)
        BATCH_JOBS_ACTIVE.dec()


# ── Main loop ────────────────────────────────────────────────────

def run_worker(poll_interval: float = 3.0) -> None:
    job_store = JobStore()
    tower_store = TowerStore()
    srtm = SRTMReader(os.getenv("SRTM_DATA_DIR", "./srtm_data"))

    logger.info(
        "Worker started  db=%s  poll=%.1fs  results=%s",
        job_store.backend, poll_interval, JOB_RESULTS_DIR,
    )

    while True:
        try:
            job = job_store.claim_next_job()
            if job is None:
                time.sleep(poll_interval)
                continue
            process_job(job, tower_store, srtm, job_store)
        except KeyboardInterrupt:
            logger.info("Worker shutting down")
            break
        except Exception:
            logger.exception("Unexpected error in worker loop")
            time.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch PDF worker")
    parser.add_argument(
        "--poll-interval", type=float, default=3.0,
        help="Seconds between job queue polls (default: 3)",
    )
    args = parser.parse_args()
    run_worker(poll_interval=args.poll_interval)
