"""
telecom_tower_power_db.py
Database-backed async API using SQLAlchemy (asyncpg / aiosqlite).

Run: uvicorn telecom_tower_power_db:app --reload
"""

import os
import json
import math
import re
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Security, UploadFile, File, Request
from fastapi.security import APIKeyHeader
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import stripe_billing
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import select
import aiohttp
import redis
from rq import Queue
from rq.job import Job
import prometheus_client
from prometheus_fastapi_instrumentator import Instrumentator
from s3_storage import download_result, get_presigned_url, result_exists

# ------------------------------
# Database setup
# ------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./towers.db")
# Auto-convert plain PostgreSQL URLs to asyncpg driver for async engine
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

_IS_POSTGRES = DATABASE_URL.startswith("postgresql")

if _IS_POSTGRES:
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert
else:
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert


class TowerModel(Base):
    __tablename__ = "towers"
    id: Mapped[str] = mapped_column(primary_key=True)
    lat: Mapped[float]
    lon: Mapped[float]
    height_m: Mapped[float]
    operator: Mapped[str]
    bands: Mapped[str]  # JSON string list
    power_dbm: Mapped[float]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await engine.dispose()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# ------------------------------
# Redis & RQ setup (RQ requires synchronous redis)
# ------------------------------
REDIS_URL = os.getenv("REDIS_URL", "")
try:
    redis_client = redis.from_url(REDIS_URL or "redis://localhost:6379")
    redis_client.ping()
    queue = Queue("batch_pdfs", connection=redis_client)
except Exception:
    redis_client = None
    queue = None

# ------------------------------
# Prometheus metrics
# ------------------------------
rate_limit_exceeded = prometheus_client.Counter(
    "rate_limit_exceeded_total", "Total rate limit exceeded", ["tier"]
)
batch_jobs_total = prometheus_client.Counter(
    "batch_jobs_total", "Total batch jobs submitted", ["status"]
)
active_batch_jobs = prometheus_client.Gauge("active_batch_jobs", "Currently active batch jobs")

# ------------------------------
# FastAPI app
# ------------------------------
app = FastAPI(title="TELECOM TOWER POWER", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# Auth
VALID_API_KEYS = json.loads(
    os.getenv("VALID_API_KEYS", '{"free_123":"free","pro_abc":"pro","ent_xyz":"enterprise"}')
)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key and api_key in VALID_API_KEYS:
        return {"key": api_key, "tier": VALID_API_KEYS[api_key]}
    # Check dynamically-registered keys from Stripe billing
    if api_key:
        dynamic = stripe_billing.lookup_key(api_key)
        if dynamic is not None:
            return {"key": api_key, "tier": dynamic["tier"]}
    raise HTTPException(403, "Invalid or missing API key")


def require_tier(*required_tiers):
    async def dependency(auth=Depends(verify_api_key)):
        if auth["tier"] not in required_tiers:
            raise HTTPException(403, f"Requires tier: {', '.join(required_tiers)}")
        return auth
    return Depends(dependency)

# ------------------------------
# Endpoints
# ------------------------------

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


@app.post("/towers")
async def add_tower(tower: dict, db: AsyncSession = Depends(get_db), auth=Depends(verify_api_key)):
    bands_json = json.dumps(tower["bands"])
    values = dict(
        id=tower["id"],
        lat=tower["lat"],
        lon=tower["lon"],
        height_m=tower["height_m"],
        operator=tower["operator"],
        bands=bands_json,
        power_dbm=tower.get("power_dbm", 43.0),
    )
    update_cols = {k: v for k, v in values.items() if k != "id"}

    stmt = _dialect_insert(TowerModel).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)

    await db.execute(stmt)
    await db.commit()
    return {"message": "Tower upserted"}


@app.get("/towers")
async def list_towers(
    operator: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth=Depends(verify_api_key),
):
    stmt = select(TowerModel)
    if operator:
        stmt = stmt.where(TowerModel.operator == operator)
    result = await db.execute(stmt)
    towers = result.scalars().all()
    return {
        "towers": [
            {
                "id": t.id,
                "lat": t.lat,
                "lon": t.lon,
                "height_m": t.height_m,
                "operator": t.operator,
                "bands": json.loads(t.bands),
                "power_dbm": t.power_dbm,
            }
            for t in towers
        ]
    }


@app.get("/towers/nearest")
async def nearest_towers(
    lat: float,
    lon: float,
    operator: Optional[str] = None,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
    auth=Depends(verify_api_key),
):
    stmt = select(TowerModel)
    if operator:
        stmt = stmt.where(TowerModel.operator == operator)
    result = await db.execute(stmt)
    towers = result.scalars().all()

    def dist(t):
        return math.hypot(t.lat - lat, t.lon - lon) * 111  # approximate km/deg

    sorted_towers = sorted(towers, key=dist)[:limit]
    return {
        "nearest_towers": [
            {"id": t.id, "lat": t.lat, "lon": t.lon, "distance_km": round(dist(t), 3)}
            for t in sorted_towers
        ]
    }


@app.post("/analyze")
async def analyze_link(
    tower_id: str,
    receiver: dict,
    db: AsyncSession = Depends(get_db),
    auth=Depends(verify_api_key),
):
    result = await db.execute(select(TowerModel).where(TowerModel.id == tower_id))
    tower = result.scalar_one_or_none()
    if not tower:
        raise HTTPException(404, "Tower not found")
    # Reuse existing LinkEngine and ElevationService (not shown for brevity)
    return {"feasible": True, "signal_dbm": -65.2}  # placeholder


@app.post("/batch_submit")
async def submit_batch(
    tower_id: str,
    csv_file: UploadFile = File(...),
    auth=require_tier("pro", "enterprise"),
):
    if queue is None:
        raise HTTPException(503, "Batch processing unavailable – Redis not configured")
    content = await csv_file.read()
    job = queue.enqueue(
        "worker.generate_batch_pdfs",
        args=(tower_id, content.decode("utf-8")),
        job_timeout=3600,
    )
    active_batch_jobs.inc()
    batch_jobs_total.labels(status="submitted").inc()
    return {"job_id": job.id}


@app.get("/batch_status/{job_id}")
async def batch_status(job_id: str, auth=Depends(verify_api_key)):
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise HTTPException(400, "Invalid job ID: path traversal detected")
    if not _SAFE_ID.match(job_id):
        raise HTTPException(400, "Invalid job ID")
    if redis_client is None:
        raise HTTPException(503, "Batch processing unavailable – Redis not configured")
    try:
        job = Job.fetch(job_id, connection=redis_client)
    except Exception:
        raise HTTPException(404, "Job not found")
    if job.is_finished:
        return {"status": "completed", "download_url": f"/batch_download/{job_id}"}
    elif job.is_failed:
        return {"status": "failed", "error": str(job.exc_info)}
    else:
        return {"status": "queued"}


@app.get("/batch_download/{job_id}")
async def batch_download(job_id: str, auth=Depends(verify_api_key)):
    # Reject path traversal attempts (..%2F is decoded before we see it)
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise HTTPException(400, "Invalid job ID: path traversal detected")
    if not _SAFE_ID.match(job_id):
        raise HTTPException(400, "Invalid job ID")

    # If S3 is configured, redirect to a presigned URL
    presigned = get_presigned_url(job_id)
    if presigned:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(presigned)

    # Fallback: stream from local storage via s3_storage
    data = download_result(job_id)
    if data is None:
        raise HTTPException(404, "Job not ready or expired")

    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=batch_{job_id}.zip"},
    )


# ------------------------------------------------------------
# Self-service signup & Stripe billing
# ------------------------------------------------------------

class SignupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)

class CheckoutRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    tier: str = Field(..., pattern="^(pro|enterprise)$")

class KeyLookupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)


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
    """Create a Stripe Checkout Session for a paid plan."""
    try:
        url = stripe_billing.create_checkout_session(body.email, body.tier)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"checkout_url": url}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Receive Stripe webhook events and provision/manage API keys."""
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
    """After Stripe Checkout, return the provisioned API key."""
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


@app.post("/signup/status")
async def signup_status(body: KeyLookupRequest):
    """Look up an existing API key by email address."""
    info = stripe_billing.get_key_info_for_email(body.email)
    if info is None:
        raise HTTPException(status_code=404, detail="No account found for this email")
    return {
        "api_key": info["api_key"],
        "tier": info["tier"],
        "email": info["email"],
        "has_subscription": info.get("stripe_subscription_id") is not None,
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "database": DATABASE_URL.split("+")[0]}
