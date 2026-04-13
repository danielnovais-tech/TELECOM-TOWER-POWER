"""
api_client.py
Typed Python client for the TELECOM TOWER POWER API.

Models are derived from the FastAPI/Pydantic schemas so that any drift
between backend and frontend is caught immediately (import errors or
validation failures) rather than silently at runtime.

Regenerate after API changes:
    python scripts/export_openapi.py        # refresh openapi.json
    # (models below mirror the backend Pydantic schemas)
"""

from __future__ import annotations

import os
from enum import Enum
from typing import List, Optional

import requests
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schema models (mirrors components/schemas in openapi.json)
# ---------------------------------------------------------------------------

class Band(str, Enum):
    BAND_700 = "700MHz"
    BAND_1800 = "1800MHz"
    BAND_2600 = "2600MHz"
    BAND_3500 = "3500MHz"


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


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)


class CheckoutRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)
    tier: str = Field(..., pattern="^(pro|enterprise)$")
    country: Optional[str] = Field(None, min_length=2, max_length=2)


class KeyLookupRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=254)


# ---------------------------------------------------------------------------
# Rate-limit exception
# ---------------------------------------------------------------------------

class RateLimitExceeded(Exception):
    """Raised when the API returns HTTP 429."""

    def __init__(self, limit: int | None = None, detail: str = ""):
        self.limit = limit
        self.detail = detail
        msg = f"Rate limit exceeded ({limit} requests/min)." if limit else "Rate limit exceeded."
        if detail:
            msg += f" {detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Typed HTTP client
# ---------------------------------------------------------------------------

class TelecomTowerAPIClient:
    """Thin typed wrapper around the REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str = "",
        timeout: int = 120,
    ):
        self.base_url = (base_url or os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self.api_key})
        # Rate-limit state (updated after every request)
        self.rate_limit_remaining: int | None = None
        self.rate_limit_limit: int | None = None

    # -- internal helpers ---------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _capture_rate_limit(self, r: requests.Response) -> None:
        """Extract X-RateLimit-* headers and raise on 429."""
        rem = r.headers.get("X-RateLimit-Remaining")
        lim = r.headers.get("X-RateLimit-Limit")
        if rem is not None:
            self.rate_limit_remaining = int(rem)
        if lim is not None:
            self.rate_limit_limit = int(lim)
        if r.status_code == 429:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:
                pass
            raise RateLimitExceeded(limit=self.rate_limit_limit, detail=detail)

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self._session.get(self._url(path), params=params, timeout=self.timeout)
        self._capture_rate_limit(r)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        r = self._session.post(self._url(path), json=json_body, params=params, timeout=self.timeout)
        self._capture_rate_limit(r)
        r.raise_for_status()
        return r.json()

    # -- typed endpoints ----------------------------------------------------

    def health(self) -> dict:
        return self._get("/health")

    def list_towers(self, operator: str | None = None, limit: int = 100, offset: int = 0) -> List[dict]:
        params: dict = {"limit": limit, "offset": offset}
        if operator:
            params["operator"] = operator
        data = self._get("/towers", params=params)
        return data.get("towers", [])

    def get_tower(self, tower_id: str) -> dict:
        return self._get(f"/towers/{tower_id}")

    def add_tower(self, tower: TowerInput) -> dict:
        return self._post("/towers", json_body=tower.model_dump())

    def update_tower(self, tower_id: str, tower: TowerInput) -> dict:
        r = self._session.put(
            self._url(f"/towers/{tower_id}"),
            json=tower.model_dump(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def delete_tower(self, tower_id: str) -> dict:
        r = self._session.delete(self._url(f"/towers/{tower_id}"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def nearest_towers(self, lat: float, lon: float, limit: int = 5) -> List[dict]:
        data = self._get("/towers/nearest", params={"lat": lat, "lon": lon, "limit": limit})
        return data.get("nearest_towers", [])

    def analyze_link(self, tower_id: str, receiver: ReceiverInput) -> LinkAnalysisResponse:
        data = self._post("/analyze", json_body=receiver.model_dump(), params={"tower_id": tower_id})
        return LinkAnalysisResponse.model_validate(data)

    def plan_repeater(self, tower_id: str, receiver: ReceiverInput, max_hops: int = 3) -> dict:
        return self._post(
            "/plan_repeater",
            json_body=receiver.model_dump(),
            params={"tower_id": tower_id, "max_hops": max_hops},
        )

    def export_report_pdf(self, tower_id: str, lat: float, lon: float,
                          height_m: float = 10.0, antenna_gain: float = 12.0) -> bytes:
        """Returns raw PDF bytes."""
        r = self._session.get(
            self._url("/export_report/pdf"),
            params={"tower_id": tower_id, "lat": lat, "lon": lon,
                     "height_m": height_m, "antenna_gain": antenna_gain},
            timeout=self.timeout,
        )
        self._capture_rate_limit(r)
        return r.content

    def batch_reports(self, tower_id: str, csv_file, filename: str = "receivers.csv",
                      receiver_height_m: float = 10.0, antenna_gain_dbi: float = 12.0):
        """Upload a CSV and get either a ZIP (sync) or a job dict (async)."""
        r = self._session.post(
            self._url("/batch_reports"),
            params={"tower_id": tower_id,
                     "receiver_height_m": receiver_height_m,
                     "antenna_gain_dbi": antenna_gain_dbi},
            files={"csv_file": (filename, csv_file, "text/csv")},
            timeout=300,
        )
        self._capture_rate_limit(r)
        return r

    def job_status(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}")

    def job_download(self, job_id: str) -> bytes:
        r = self._session.get(self._url(f"/jobs/{job_id}/download"), timeout=self.timeout)
        self._capture_rate_limit(r)
        return r.content

    def signup_free(self, email: str) -> dict:
        return self._post("/signup/free", json_body=SignupRequest(email=email).model_dump())

    def signup_checkout(self, email: str, tier: str, country: str | None = None) -> dict:
        body = CheckoutRequest(email=email, tier=tier, country=country)
        return self._post("/signup/checkout", json_body=body.model_dump(exclude_none=True))

    def signup_success(self, session_id: str) -> dict:
        return self._get("/signup/success", params={"session_id": session_id})

    def signup_status(self, email: str) -> dict:
        return self._post("/signup/status", json_body=KeyLookupRequest(email=email).model_dump())
