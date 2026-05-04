# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""FastAPI router exposing the ``rf_engines`` registry.

Mounted under ``/coverage/engines`` by ``telecom_tower_power_api``.

Endpoints
---------
* ``GET  /coverage/engines``         — list registered engines + availability.
* ``POST /coverage/engines/predict`` — run a single named engine.
* ``POST /coverage/engines/compare`` — A/B run all (or a subset) of
  engines and return dB deltas against a reference.

Auth: the router is mounted **after** the API-key dependency in
``telecom_tower_power_api.py`` so every call is gated by an
authenticated key, same posture as the rest of ``/coverage/*``.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from rf_engines import get_engine, list_engines
from rf_engines.compare import compare as _compare


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coverage/engines", tags=["coverage-engines"])


# --- Schemas ----------------------------------------------------------------


class EngineInfo(BaseModel):
    name: str
    available: bool


class LinkInput(BaseModel):
    f_hz: float = Field(..., gt=0, description="Carrier frequency (Hz)")
    d_km: List[float] = Field(..., min_length=2)
    h_m: List[float] = Field(..., min_length=2)
    htg: float = Field(..., description="Tx antenna height AGL (m)")
    hrg: float = Field(..., description="Rx antenna height AGL (m)")
    phi_t: float = Field(..., ge=-90, le=90)
    lam_t: float = Field(..., ge=-180, le=180)
    phi_r: float = Field(..., ge=-90, le=90)
    lam_r: float = Field(..., ge=-180, le=180)
    clutter_heights_m: Optional[List[float]] = None
    pol: Optional[int] = Field(default=None, ge=1, le=2)
    zone: Optional[int] = Field(default=None, ge=1, le=4)
    time_pct: Optional[float] = Field(default=None, gt=0, lt=100)
    loc_pct: Optional[float] = Field(default=None, gt=0, lt=100)


class PredictRequest(LinkInput):
    engine: str = Field(..., description="Engine name (see GET /coverage/engines)")


class PredictResponse(BaseModel):
    engine: str
    basic_loss_db: float
    confidence: float
    extra: dict


class CompareRequest(LinkInput):
    engines: Optional[List[str]] = Field(
        default=None,
        description="Restrict comparison to these engines. Default: all available."
    )
    reference: str = Field(default="itu-p1812")


# --- Routes -----------------------------------------------------------------


@router.get("", response_model=List[EngineInfo])
async def list_registered_engines() -> List[EngineInfo]:
    return [EngineInfo(name=e.name, available=e.is_available()) for e in list_engines()]


@router.post("/predict", response_model=PredictResponse)
async def predict_one(req: PredictRequest) -> PredictResponse:
    try:
        eng = get_engine(req.engine)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown engine: {req.engine}") from exc
    if not eng.is_available():
        raise HTTPException(status_code=503, detail=f"engine unavailable: {req.engine}")
    link = req.model_dump(exclude={"engine"})
    est = eng.predict_basic_loss(**link)
    if est is None:
        raise HTTPException(status_code=422, detail="engine returned no estimate (out of domain or runtime error)")
    return PredictResponse(
        engine=est.engine,
        basic_loss_db=est.basic_loss_db,
        confidence=est.confidence,
        extra=est.extra,
    )


@router.post("/compare")
async def compare_engines(req: CompareRequest) -> dict:
    link = req.model_dump(exclude={"engines", "reference"})
    result = _compare(
        engine_names=req.engines,
        reference=req.reference,
        **link,
    )
    return result.to_dict()


# ─────────────────────────────────────────────────────────────────────
# Sionna RT — async per-pixel loss raster (kick + poll)
# ─────────────────────────────────────────────────────────────────────
# POST /coverage/engines/sionna-rt/raster — enqueues an SQS job for the
# GPU worker (scripts/sionna_rt_worker.py --poll), returning a job_id.
# GET  /coverage/engines/sionna-rt/raster/{job_id} — poll for status +
# the presigned URL to the resulting .npz raster.
#
# The endpoint is intentionally async-only: the trace is GPU-bound and
# can take seconds-to-minutes; HTTP timeouts on ALB/Caddy would kill
# any synchronous variant. The contract mirrors the existing
# ``POST /plan_repeater/async`` + ``GET /plan_repeater/jobs/{id}`` pair.

# Required env vars (the API container refuses to enqueue without them).
_QUEUE_URL_ENV = "SIONNA_RT_QUEUE_URL"
_RESULTS_BUCKET_ENV = "SIONNA_RT_RESULTS_BUCKET"
_RESULTS_PREFIX_ENV = "SIONNA_RT_RESULTS_PREFIX"  # default 'sionna-rt-rasters/'
_PRESIGN_TTL_S = int(os.getenv("SIONNA_RT_PRESIGN_TTL_S", "3600"))

# Sionna RT is GPU-expensive. Restrict it to paying tiers from BUSINESS up;
# FREE/STARTER/PRO callers get a hard 403 even though they could otherwise
# hit /coverage/engines/* (the registry's cheap engines remain open). Admin
# keys bypass the gate, mirroring the rest of the API.
_RT_ALLOWED_TIERS = frozenset({"business", "enterprise", "ultra"})

# Per-tier cell-count caps for the loss raster. Distinct from
# `_HEATMAP_MAX_CELLS` in telecom_tower_power_api.py because RT is roughly
# an order of magnitude more expensive than the regression heatmap. The
# global hard ceiling (4M cells) in `_RasterGridIn` still applies.
_RT_RASTER_MAX_CELLS = {
    "business":     40_000,    # 200 x 200
    "enterprise":  160_000,    # 400 x 400
    "ultra":       640_000,    # 800 x 800
}

# In-memory job tracker. Lightweight: jobs are short-lived and the
# authoritative state lives on SQS + S3. The dict only carries enough
# metadata to: (a) enforce per-tenant ownership on GET, (b) build the
# presigned URL once the result object exists. TTL reaper runs inline.
_JOBS_TTL_S = int(os.getenv("SIONNA_RT_JOBS_TTL_S", "1800"))   # 30 min
_JOBS_MAX = int(os.getenv("SIONNA_RT_JOBS_MAX", "1024"))
_jobs: Dict[str, Dict[str, Any]] = {}

# Lazy boto3 clients — keep import out of the API cold-start path.
_sqs_client = None
_s3_client = None


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3  # type: ignore[import-not-found]
        _sqs_client = boto3.client(
            "sqs", region_name=os.getenv("AWS_REGION", "sa-east-1"),
        )
    return _sqs_client


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3  # type: ignore[import-not-found]
        _s3_client = boto3.client(
            "s3", region_name=os.getenv("AWS_REGION", "sa-east-1"),
        )
    return _s3_client


def _reap_jobs(now: Optional[float] = None) -> None:
    """Drop completed/failed/stale jobs older than TTL, oldest first."""
    now = now if now is not None else time.time()
    cutoff = now - _JOBS_TTL_S
    stale = [
        jid for jid, j in _jobs.items()
        if (j.get("created_at") or 0) < cutoff
    ]
    for jid in stale:
        _jobs.pop(jid, None)
    # Hard cap (defence in depth against runaway dispatches).
    if len(_jobs) > _JOBS_MAX:
        # Drop oldest until back under cap.
        for jid in sorted(_jobs, key=lambda k: _jobs[k].get("created_at") or 0)[
            : len(_jobs) - _JOBS_MAX
        ]:
            _jobs.pop(jid, None)


# ── Schemas ────────────────────────────────────────────────────────

class _TxIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    height_m: float = Field(..., ge=0, le=500)
    power_dbm: float = Field(default=43.0, ge=0, le=80)


class _RasterGridIn(BaseModel):
    rows: int = Field(..., ge=2, le=2000)
    cols: int = Field(..., ge=2, le=2000)
    bbox: List[float] = Field(
        ...,
        min_length=4, max_length=4,
        description="[south, west, north, east]",
    )

    @model_validator(mode="after")
    def _check_bbox(self) -> "_RasterGridIn":
        s, w, n, e = self.bbox
        if not (-90 <= s < n <= 90):
            raise ValueError(f"bbox south/north invalid: {s}, {n}")
        if not (-180 <= w < e <= 180):
            raise ValueError(f"bbox west/east invalid: {w}, {e}")
        if self.rows * self.cols > 4_000_000:
            raise ValueError(
                f"raster_grid too large ({self.rows}x{self.cols} > 4M cells); "
                "split before submitting"
            )
        return self


class SionnaRTRasterRequest(BaseModel):
    """Submission body for ``POST /coverage/engines/sionna-rt/raster``.

    ``scene_s3_uri`` points at the directory produced by
    ``scripts/build_mitsuba_scene.py --emit-scene`` (must contain a
    ``manifest.json`` with ``implementation_status='complete'``).
    """

    scene_s3_uri: str = Field(
        ..., min_length=8, max_length=1024,
        description="s3:// URI of the scene-bundle directory",
    )
    tx: _TxIn
    frequency_hz: float = Field(..., gt=1e6, le=3e11)
    raster_grid: _RasterGridIn

    @model_validator(mode="after")
    def _check_scene_uri(self) -> "SionnaRTRasterRequest":
        if not self.scene_s3_uri.startswith("s3://"):
            raise ValueError("scene_s3_uri must start with s3://")
        return self


class SionnaRTRasterAccepted(BaseModel):
    job_id: str
    status: str
    poll_url: str
    result_s3_uri: str


class SionnaRTRasterStatus(BaseModel):
    job_id: str
    status: str  # queued | done | not-found
    submitted_at: float
    finished_at: Optional[float] = None
    result_s3_uri: Optional[str] = None
    raster_url: Optional[str] = None  # presigned, only when status == 'done'
    raster_bytes: Optional[int] = None
    error: Optional[str] = None


def _build_result_s3_uri(job_id: str) -> tuple[str, str, str]:
    bucket = os.getenv(_RESULTS_BUCKET_ENV, "")
    prefix = os.getenv(_RESULTS_PREFIX_ENV, "sionna-rt-rasters/")
    if not bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                f"sionna-rt raster pipeline misconfigured: "
                f"set ${_RESULTS_BUCKET_ENV}"
            ),
        )
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    key = f"{prefix}{job_id}.npz"
    return bucket, key, f"s3://{bucket}/{key}"


def _resolve_queue_url() -> str:
    q = os.getenv(_QUEUE_URL_ENV, "")
    if not q:
        raise HTTPException(
            status_code=503,
            detail=f"sionna-rt raster pipeline misconfigured: set ${_QUEUE_URL_ENV}",
        )
    return q


def _auth_context(request: Request) -> tuple[str, Optional[str], Optional[str], bool]:
    """Pull (tier, owner, api_key, is_admin) off ``request.state``.

    The API container's ``verify_api_key`` populates these (see
    telecom_tower_power_api.py); the router is mounted *after* that
    dependency so the values are guaranteed to be present in production.
    Tests can stub them via a tiny ASGI middleware on the test app.
    """
    state = request.state
    tier = (getattr(state, "tier", None) or "").lower()
    owner = getattr(state, "owner", None)
    api_key = getattr(state, "api_key", None)
    is_admin = bool(getattr(state, "is_admin", False))
    return tier, owner, api_key, is_admin


def _enforce_rt_tier(tier: str, is_admin: bool) -> None:
    if is_admin:
        return
    if tier not in _RT_ALLOWED_TIERS:
        raise HTTPException(
            status_code=403,
            detail=(
                "sionna-rt raster requires Business / Enterprise / Ultra tier. "
                f"Your tier: {tier or 'unknown'}"
            ),
        )


def _enforce_rt_cell_cap(rows: int, cols: int, tier: str, is_admin: bool) -> None:
    if is_admin:
        return
    cap = _RT_RASTER_MAX_CELLS.get(tier)
    if cap is None:
        # Unknown / disallowed tier — _enforce_rt_tier will already
        # have raised; this is defence in depth.
        raise HTTPException(status_code=403, detail="sionna-rt raster: tier not allowed")
    if rows * cols > cap:
        raise HTTPException(
            status_code=403,
            detail=(
                f"raster_grid {rows}x{cols} ({rows * cols} cells) exceeds "
                f"{tier} tier cap of {cap} cells. Reduce the grid or upgrade."
            ),
        )


async def _audit_log_safe(api_key: Optional[str], action: str, **fields: Any) -> None:
    """Best-effort audit logging — never fails the request."""
    if not api_key:
        return
    try:
        import audit_log as _audit  # lazy: keeps this module standalone-importable
        await _audit.log(api_key, action, **fields)
    except Exception:
        logger.exception("rf_engines audit log failed for %s", action)


@router.post("/sionna-rt/raster",
             response_model=SionnaRTRasterAccepted, status_code=202)
async def sionna_rt_raster_submit(
    req: SionnaRTRasterRequest,
    request: Request,
) -> SionnaRTRasterAccepted:
    """Enqueue a per-pixel loss-raster job for the Sionna RT GPU worker.

    Returns ``202 Accepted`` with a ``job_id`` and ``poll_url``. The
    raster lands at ``result_s3_uri`` once the worker is done; poll the
    status endpoint for a presigned URL.

    Restricted to Business / Enterprise / Ultra (RT is the GPU-priced
    engine). Cell-count caps are per-tier. Status code is intentionally
    ``503`` (not ``500``) when ops haven't set ``$SIONNA_RT_QUEUE_URL``
    / ``$SIONNA_RT_RESULTS_BUCKET`` — the feature is correctly
    *configured* off by default until the worker pool is provisioned.
    """
    tier, owner, api_key, is_admin = _auth_context(request)
    _enforce_rt_tier(tier, is_admin)
    _enforce_rt_cell_cap(req.raster_grid.rows, req.raster_grid.cols, tier, is_admin)

    queue_url = _resolve_queue_url()
    job_id = uuid.uuid4().hex
    _, _, result_s3_uri = _build_result_s3_uri(job_id)

    scene_uri = req.scene_s3_uri if req.scene_s3_uri.endswith("/") \
        else req.scene_s3_uri + "/"

    body = {
        "job_id": job_id,
        "scene_s3_uri": scene_uri,
        "result_s3_uri": result_s3_uri,
        "frequency_hz": float(req.frequency_hz),
        "tx": req.tx.model_dump(),
        "raster_grid": req.raster_grid.model_dump(),
    }
    try:
        _get_sqs().send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))
    except Exception as ex:
        logger.exception("sionna-rt raster submit: SQS send failed")
        raise HTTPException(
            status_code=502, detail=f"queue send failed: {type(ex).__name__}",
        ) from ex

    now = time.time()
    _reap_jobs(now)
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        # OWASP A01: lock the job to the caller's owner so cross-tenant
        # polls return 404 (not 403) on the GET handler — see below.
        "owner": owner,
        "result_s3_uri": result_s3_uri,
        "scene_s3_uri": scene_uri,
        "created_at": now,
        "frequency_hz": float(req.frequency_hz),
        "rows": req.raster_grid.rows,
        "cols": req.raster_grid.cols,
    }
    await _audit_log_safe(
        api_key,
        "coverage.rt.raster.submit",
        tier=tier,
        target=f"job:{job_id}",
        ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        metadata={
            "rows": req.raster_grid.rows,
            "cols": req.raster_grid.cols,
            "frequency_hz": float(req.frequency_hz),
            "scene_s3_uri": scene_uri,
        },
    )
    return SionnaRTRasterAccepted(
        job_id=job_id,
        status="queued",
        poll_url=f"/coverage/engines/sionna-rt/raster/{job_id}",
        result_s3_uri=result_s3_uri,
    )


@router.get("/sionna-rt/raster/{job_id}",
            response_model=SionnaRTRasterStatus)
async def sionna_rt_raster_status(
    job_id: str,
    request: Request,
) -> SionnaRTRasterStatus:
    """Return the current status of a Sionna RT raster job.

    Authoritative state for ``done`` is the existence of the
    ``result_s3_uri`` object on S3 — the API container never sees the
    worker directly. While the object is missing the job stays
    ``queued``; once it appears we return a presigned download URL
    valid for ``$SIONNA_RT_PRESIGN_TTL_S`` seconds (default 1 h).

    Cross-tenant polls return ``404`` (OWASP A01 — never disclose
    job_id existence to non-owners).
    """
    tier, owner, api_key, is_admin = _auth_context(request)
    j = _jobs.get(job_id)
    # Combine "missing" and "wrong owner" into a single 404 so an
    # attacker can't distinguish "job never existed" from "belongs to
    # another tenant" by timing or response shape.
    if not j or (
        not is_admin
        and j.get("owner") is not None
        and j.get("owner") != owner
    ):
        raise HTTPException(status_code=404, detail="job not found or expired")

    s3 = _get_s3()
    bucket, key, result_uri = _build_result_s3_uri(job_id)
    status = j["status"]
    finished_at: Optional[float] = j.get("finished_at")
    raster_url: Optional[str] = None
    raster_bytes: Optional[int] = None
    transitioned_to_done = False
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        raster_bytes = int(head.get("ContentLength") or 0)
        raster_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGN_TTL_S,
        )
        if status != "done":
            transitioned_to_done = True
        status = "done"
        if finished_at is None:
            finished_at = time.time()
            j["finished_at"] = finished_at
            j["status"] = "done"
    except Exception:
        logger.debug("sionna-rt raster head_object miss for %s", job_id)
    # Audit only on terminal-state transitions to keep the table sane —
    # callers may poll dozens of times before the worker finishes.
    if transitioned_to_done:
        await _audit_log_safe(
            api_key,
            "coverage.rt.raster.poll",
            tier=tier,
            target=f"job:{job_id}",
            ip=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
            metadata={"status": "done", "raster_bytes": raster_bytes},
        )
    return SionnaRTRasterStatus(
        job_id=job_id,
        status=status,
        submitted_at=j["created_at"],
        finished_at=finished_at,
        result_s3_uri=result_uri,
        raster_url=raster_url,
        raster_bytes=raster_bytes,
    )
