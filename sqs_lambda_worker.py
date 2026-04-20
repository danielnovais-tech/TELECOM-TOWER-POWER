"""
sqs_lambda_worker.py – AWS Lambda handler triggered by SQS.

Processes batch PDF generation jobs from an SQS queue. Each message
contains a job_id referencing a row in the batch_jobs table.

Architecture:
    API Gateway → ECS API (creates job + enqueues SQS message)
        → SQS Queue → this Lambda → S3 (ZIP result)
        → updates batch_jobs in PostgreSQL (via RDS Proxy)

Environment variables:
    DATABASE_URL        – PostgreSQL connection string (fallback when RDS Proxy not set)
    RDS_PROXY_HOST      – RDS Proxy endpoint (enables IAM auth + connection pooling)
    RDS_PROXY_PORT      – RDS Proxy port (default: 5432)
    DB_NAME             – Database name (default: telecom_tower_power)
    DB_USER             – Database user for IAM auth (default: telecom_admin)
    S3_BUCKET_NAME      – S3 bucket for report output
    S3_PREFIX           – Key prefix (default: "batch-results/")
    SRTM_DATA_DIR       – Local dir for SRTM tiles (default: /tmp/srtm_data)
"""

import io
import json
import logging
import math
import os
import time
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment ──────────────────────────────────────────────────
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "batch-results/")
SRTM_DATA_DIR = os.environ.get("SRTM_DATA_DIR", "/tmp/srtm_data")

# RDS Proxy config (preferred – connection pooling + IAM auth)
RDS_PROXY_HOST = os.environ.get("RDS_PROXY_HOST", "")
RDS_PROXY_PORT = int(os.environ.get("RDS_PROXY_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "telecom_tower_power")
DB_USER = os.environ.get("DB_USER", "telecom_admin")
_USE_RDS_PROXY = bool(RDS_PROXY_HOST)

# Fallback: direct DATABASE_URL (no proxy)
_RAW_DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = _RAW_DATABASE_URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    DATABASE_URL = DATABASE_URL.replace("postgresql+aiosqlite://", "")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Lazy-initialised clients
_s3 = None
_db_conn = None
_USE_PG = _USE_RDS_PROXY or bool(DATABASE_URL)
_rds_client = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _generate_rds_auth_token() -> str:
    """Generate a short-lived IAM auth token for RDS Proxy connection."""
    global _rds_client
    if _rds_client is None:
        _rds_client = boto3.client("rds", region_name=os.environ.get("AWS_REGION", "sa-east-1"))
    return _rds_client.generate_db_auth_token(
        DBHostname=RDS_PROXY_HOST,
        Port=RDS_PROXY_PORT,
        DBUsername=DB_USER,
    )


def _get_db():
    """Return a psycopg2 connection, using RDS Proxy IAM auth when available.

    When RDS_PROXY_HOST is set:
      - Generates a short-lived IAM auth token (valid ~15 min)
      - Connects with SSL required (RDS Proxy enforces TLS)
      - Token is regenerated on each new connection (cold start or reconnect)

    When only DATABASE_URL is set:
      - Falls back to direct psycopg2 connection (password in URL)

    Connection is reused across warm Lambda invocations.
    """
    global _db_conn
    import psycopg2

    if _db_conn is not None and not _db_conn.closed:
        try:
            # Verify connection is still alive (proxy may have closed it)
            _db_conn.cursor().execute("SELECT 1")
            return _db_conn
        except Exception:
            logger.warning("Stale DB connection detected, reconnecting...")
            try:
                _db_conn.close()
            except Exception:
                pass
            _db_conn = None

    if _USE_RDS_PROXY:
        token = _generate_rds_auth_token()
        _db_conn = psycopg2.connect(
            host=RDS_PROXY_HOST,
            port=RDS_PROXY_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=token,
            sslmode="require",
        )
        logger.info("Connected to RDS via Proxy (IAM auth): %s", RDS_PROXY_HOST)
    else:
        _db_conn = psycopg2.connect(DATABASE_URL)
        logger.info("Connected to database directly (DATABASE_URL)")

    _db_conn.autocommit = False
    return _db_conn


# Lazy-init for local/SQLite fallback via existing abstractions
_job_store = None
_tower_store = None


def _get_job_store():
    global _job_store
    if _job_store is None:
        from job_store import JobStore
        _job_store = JobStore()
    return _job_store


def _get_tower_store():
    global _tower_store
    if _tower_store is None:
        from tower_db import TowerStore
        _tower_store = TowerStore()
    return _tower_store


# ── Domain models (lightweight copies to avoid importing full API) ─

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


@dataclass(frozen=True)
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float
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


# ── Link analysis engine ─────────────────────────────────────────

class LinkEngine:
    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def free_space_path_loss(d_km, f_hz):
        d_m = d_km * 1000
        return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km, f_hz, d1_km, d2_km):
        d1, d2 = d1_km * 1000, d2_km * 1000
        c = 299792458
        return math.sqrt((c * d1 * d2) / (f_hz * (d1 + d2)))

    @staticmethod
    def terrain_clearance(terrain_profile, d_km, f_hz, tx_h, rx_h, k_factor=1.33):
        """k_factor: effective Earth radius factor (4/3 standard atmosphere)."""
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n - 1)
        R_eff = 6371.0 * k_factor  # effective Earth radius in km
        min_clearance = float("inf")
        for i, ground_h in enumerate(terrain_profile):
            d_i = i * step
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            # Earth curvature correction: bulge = d1*d2 / (2*R_eff)
            d1, d2 = d_i, d_km - d_i
            d1_m = d1 * 1000
            d2_m = d2 * 1000
            earth_bulge = (d1_m * d2_m) / (2 * R_eff * 1000)
            clearance = line_h - ground_h - earth_bulge
            if d1 <= 0 or d2 <= 0:
                continue
            fr = LinkEngine.fresnel_radius(d_km, f_hz, d1, d2)
            if fr > 0:
                min_clearance = min(min_clearance, clearance / fr)
        return min_clearance if min_clearance != float("inf") else 1.0

    @staticmethod
    def estimate_signal(tx_power_dbm, tx_gain_dbi, rx_gain_dbi, f_hz, d_km, extra_loss_db=0.0):
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl - extra_loss_db


# ── Terrain + Link helpers ───────────────────────────────────────

def _get_terrain_profile_sync(srtm, lat1, lon1, lat2, lon2, num_points=30):
    heights = []
    for i in range(num_points):
        frac = i / (num_points - 1)
        lat = lat1 + (lat2 - lat1) * frac
        lon = lon1 + (lon2 - lon1) * frac
        elev = srtm.get_elevation(lat, lon)
        heights.append(elev if elev is not None else 0.0)
    return heights


def _analyze_link_sync(tower, receiver, terrain_profile):
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
        rec = (
            f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). "
            f"Increase receiver height to > {tower.height_m + 10:.0f}m or move tower."
        )
    elif rssi < -95:
        rec = (
            f"Signal too low ({rssi:.1f} dBm). "
            f"Consider higher gain antenna or use a repeater tower at distance {d_km / 2:.1f}km."
        )
    else:
        rec = f"Good link. RSSI = {rssi:.1f} dBm. Clear LOS."

    return LinkResult(
        feasible=feasible, signal_dbm=rssi, fresnel_clearance=fresnel_clear,
        los_ok=los_ok, distance_km=d_km, recommendation=rec,
        terrain_profile=terrain_profile if terrain_profile else None,
        tx_height_asl=tx_h_asl if terrain_profile else None,
        rx_height_asl=rx_h_asl if terrain_profile else None,
    )


# ── DB helpers ───────────────────────────────────────────────────

def _update_job_status(job_id: str, status: str, **kwargs):
    """Update a batch_jobs row."""
    if not _USE_PG:
        store = _get_job_store()
        if status == "completed":
            store.complete_job(job_id, kwargs.get("result_path", ""))
        elif status == "failed":
            store.fail_job(job_id, kwargs.get("error", ""))
        elif status == "running":
            # Mark as running by updating via raw update
            import sqlite3, time as _t
            conn = sqlite3.connect(store.db_path)
            conn.execute("UPDATE batch_jobs SET status = 'running', updated_at = ? WHERE id = ?", (_t.time(), job_id))
            conn.commit()
            conn.close()
        return

    conn = _get_db()
    sets = ["status = %s", "updated_at = %s"]
    vals: list = [status, time.time()]
    for col, val in kwargs.items():
        sets.append(f"{col} = %s")
        vals.append(val)
    vals.append(job_id)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE batch_jobs SET {', '.join(sets)} WHERE id = %s",
            vals,
        )
    conn.commit()


def _update_progress(job_id: str, progress: int):
    if not _USE_PG:
        _get_job_store().update_progress(job_id, progress)
        return

    conn = _get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE batch_jobs SET progress = %s, updated_at = %s WHERE id = %s",
            (progress, time.time(), job_id),
        )
    conn.commit()


def _fetch_tower(tower_id: str) -> Optional[Dict[str, Any]]:
    if not _USE_PG:
        return _get_tower_store().get(tower_id)

    conn = _get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id, lat, lon, height_m, operator, bands, power_dbm FROM towers WHERE id = %s", (tower_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "lat": row[1], "lon": row[2], "height_m": row[3],
        "operator": row[4], "bands": row[5], "power_dbm": row[6],
    }


def _fetch_job(job_id: str) -> Optional[Dict[str, Any]]:
    if not _USE_PG:
        return _get_job_store().get_job(job_id)

    conn = _get_db()
    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM batch_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


# ── Core job processor ───────────────────────────────────────────

def _process_single_job(job_id: str, tier: str = ""):
    """Process one batch job: generate PDFs → ZIP → S3."""
    from pdf_generator import build_pdf_report
    from srtm_elevation import SRTMReader

    logger.info("Processing job %s", job_id)
    start = time.monotonic()

    # Mark as running
    _update_job_status(job_id, "running")

    job = _fetch_job(job_id)
    if job is None:
        logger.error("Job %s not found in DB", job_id)
        return

    tower_id = job["tower_id"]
    tower_row = _fetch_tower(tower_id)
    if tower_row is None:
        _update_job_status(job_id, "failed", error=f"Tower {tower_id} not found")
        return

    # Parse bands (stored as JSON string or comma-separated)
    bands_raw = tower_row["bands"]
    if isinstance(bands_raw, str):
        try:
            bands_list = json.loads(bands_raw)
        except json.JSONDecodeError:
            bands_list = [b.strip() for b in bands_raw.split(",")]
    else:
        bands_list = bands_raw

    tower = Tower(
        id=tower_row["id"], lat=tower_row["lat"], lon=tower_row["lon"],
        height_m=tower_row["height_m"], operator=tower_row["operator"],
        bands=[Band(b) for b in bands_list],
        power_dbm=tower_row["power_dbm"],
    )
    freq_mhz = tower.primary_freq_hz() / 1e6

    receivers_data = json.loads(job["receivers"])

    # Ensure SRTM data dir exists
    os.makedirs(SRTM_DATA_DIR, exist_ok=True)
    srtm = SRTMReader(SRTM_DATA_DIR)

    # Generate PDFs sequentially (Lambda has limited /tmp and no multiprocessing)
    pdf_results: Dict[int, bytes] = {}

    for idx, rx_data in enumerate(receivers_data):
        try:
            rx = Receiver(
                lat=rx_data["lat"], lon=rx_data["lon"],
                height_m=rx_data.get("height_m", 10.0),
                antenna_gain_dbi=rx_data.get("antenna_gain_dbi", 12.0),
            )
            terrain = _get_terrain_profile_sync(srtm, tower.lat, tower.lon, rx.lat, rx.lon)
            result = _analyze_link_sync(tower, rx, terrain)
            pdf_buf = build_pdf_report(tower, rx, result, terrain, freq_mhz)
            pdf_results[idx] = pdf_buf.getvalue()
        except Exception:
            logger.exception("Failed to generate PDF for receiver %d in job %s", idx, job_id)

        # Update progress every 10 receivers or on last
        if (idx + 1) % 10 == 0 or idx == len(receivers_data) - 1:
            _update_progress(job_id, idx + 1)

    # Build ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx in range(len(receivers_data)):
            if idx in pdf_results:
                filename = f"report_{tower_id}_{idx + 1:03d}.pdf"
                zf.writestr(filename, pdf_results[idx])
    zip_buf.seek(0)
    zip_bytes = zip_buf.getvalue()

    # Upload to S3 (tier-prefixed for lifecycle retention rules)
    tier_segment = f"{tier}/" if tier else ""
    s3_key = f"{S3_PREFIX}{tier_segment}{job_id}/report.zip"
    _get_s3().put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=zip_bytes,
        ContentType="application/zip",
    )

    s3_path = f"s3://{S3_BUCKET}/{s3_key}"
    _update_job_status(
        job_id, "completed",
        result_path=s3_path,
    )

    elapsed = time.monotonic() - start
    logger.info(
        "Job %s completed: %d/%d PDFs, %.1fs, %s",
        job_id, len(pdf_results), len(receivers_data), elapsed, s3_path,
    )


# ── Lambda entry point ──────────────────────────────────────────

def handler(event, context):
    """AWS Lambda handler triggered by SQS.

    Each SQS message body is a JSON object with at least ``job_id``.
    If the message also contains ``tower_id`` and ``receivers``, the
    handler creates the job row first (for direct API Gateway → SQS
    invocations that bypass the ECS API).
    """
    records = event.get("Records", [])
    logger.info("Received %d SQS record(s)", len(records))

    failed_message_ids = []

    for record in records:
        message_id = record.get("messageId", "unknown")
        try:
            body = json.loads(record["body"])
            job_id = body["job_id"]

            # If the message contains full job data (direct enqueue mode),
            # create the job row if it doesn't exist yet.
            if "tower_id" in body and "receivers" in body:
                existing = _fetch_job(job_id)
                if existing is None:
                    receivers_json = json.dumps(body["receivers"])
                    if _USE_PG:
                        conn = _get_db()
                        now = time.time()
                        with conn.cursor() as cur:
                            cur.execute(
                                """INSERT INTO batch_jobs
                                   (id, status, progress, total, tower_id, receivers,
                                    created_at, updated_at)
                                   VALUES (%s, 'queued', 0, %s, %s, %s, %s, %s)
                                   ON CONFLICT (id) DO NOTHING""",
                                (job_id, len(body["receivers"]), body["tower_id"],
                                 receivers_json, now, now),
                            )
                        conn.commit()
                    else:
                        _get_job_store().create_job(
                            job_id, body["tower_id"],
                            receivers_json, len(body["receivers"]),
                        )

            _process_single_job(job_id, tier=body.get("tier", ""))

        except Exception:
            logger.exception("Failed to process SQS message %s", message_id)
            failed_message_ids.append(message_id)

    # Partial batch failure reporting – tells SQS to retry only failed messages
    if failed_message_ids:
        return {
            "batchItemFailures": [
                {"itemIdentifier": mid} for mid in failed_message_ids
            ]
        }

    return {"statusCode": 200, "body": "OK"}
