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

from typing import List, Optional, Sequence

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rf_engines import get_engine, list_engines
from rf_engines.compare import compare as _compare


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
