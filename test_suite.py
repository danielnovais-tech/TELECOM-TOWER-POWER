#!/usr/bin/env python3
"""
test_suite.py – Comprehensive test suite for TELECOM TOWER POWER API.

Covers:
  - Health & Auth (valid/invalid/missing keys, 401/403 responses)
  - CRUD Operations (create, read, update, delete towers)
  - API Endpoints (list, filter, nearest, analyze)
  - Tier-gating Validation (batch_reports, bedrock, export_report)
  - Rate limiting (per-tier 429 enforcement)

Usage:
  python3 test_suite.py                    # local dev (default)
  python3 test_suite.py --env production   # production
  python3 test_suite.py --env staging      # staging
  python3 test_suite.py --verbose          # detailed output
  python3 test_suite.py --env local --verbose
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

ENV_URLS: Dict[str, str] = {
    "local":      "http://localhost:8000",
    "staging":    "https://staging.telecomtowerpower.com.br",
    "production": "https://api.telecomtowerpower.com.br",
}

# Demo keys that are always present when ENABLE_DEMO_KEYS=true (local/staging).
# These are the hardcoded keys from telecom_tower_power_api.py.
DEMO_KEYS: Dict[str, str] = {
    "free":       "demo-key-free-001",
    "pro":        "demo-key-pro-001",
    "enterprise": "demo-key-enterprise-001",
}

# Tier-specific batch row limits (from telecom_tower_power_api.py / env vars)
BATCH_LIMIT_PRO        = 2_000
BATCH_LIMIT_ENTERPRISE = 10_000

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    category: str
    expected: Any
    got: Any
    passed: bool
    detail: str = ""
    skipped: bool = False
    skip_reason: str = ""


_results: List[TestResult] = []
_verbose: bool = False


def _log(msg: str) -> None:
    if _verbose:
        print(f"    {msg}")


# ---------------------------------------------------------------------------
# Key provisioning
# ---------------------------------------------------------------------------

class KeyStore:
    """Holds API keys for each tier, provisioned at test-session start."""

    def __init__(self) -> None:
        self.keys: Dict[str, Optional[str]] = {
            "free":       None,
            "pro":        None,
            "enterprise": None,
        }
        self._run_id = uuid.uuid4().hex[:8]

    def provision(self, base_url: str, use_demo: bool = True) -> None:
        """
        Attempt to provision keys for each tier.

        Strategy (in order):
          1. If use_demo=True, try the hardcoded demo keys first (fast, no side-effects).
          2. For free tier: call POST /signup/free with a unique test email.
          3. For pro/enterprise: demo keys only (Stripe checkout cannot be automated).
             If demo keys are unavailable, those tiers are marked as None and
             tier-gated tests are skipped gracefully.
        """
        if use_demo:
            for tier, key in DEMO_KEYS.items():
                if self._validate_key(base_url, key):
                    self.keys[tier] = key
                    _log(f"[keys] {tier}: using demo key {key!r}")
                else:
                    _log(f"[keys] {tier}: demo key {key!r} rejected by server")

        # If free key still missing, register via /signup/free
        if self.keys["free"] is None:
            email = f"test-free-{self._run_id}@test.invalid"
            key = self._register_free(base_url, email)
            if key:
                self.keys["free"] = key
                _log(f"[keys] free: registered new key via /signup/free ({email})")
            else:
                _log("[keys] free: could not obtain a free key")

        # Pro / enterprise: no automated Stripe checkout – skip if demo unavailable
        for tier in ("pro", "enterprise"):
            if self.keys[tier] is None:
                _log(f"[keys] {tier}: no key available – tier-gated tests will be skipped")

    def _validate_key(self, base_url: str, key: str) -> bool:
        """Return True if the key is accepted by GET /health (no auth) then GET /towers."""
        try:
            r = requests.get(
                f"{base_url}/towers",
                headers={"X-API-Key": key},
                timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    def _register_free(self, base_url: str, email: str) -> Optional[str]:
        """Call POST /signup/free and return the api_key, or None on failure."""
        try:
            r = requests.post(
                f"{base_url}/signup/free",
                json={"email": email},
                timeout=10,
            )
            if r.status_code == 201:
                return r.json().get("api_key")
            _log(f"[keys] /signup/free returned {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            _log(f"[keys] /signup/free exception: {exc}")
        return None

    def header(self, tier: str) -> Dict[str, str]:
        key = self.keys.get(tier)
        if not key:
            return {}
        return {"X-API-Key": key}

    def available(self, tier: str) -> bool:
        return bool(self.keys.get(tier))


# ---------------------------------------------------------------------------
# Test runner helpers
# ---------------------------------------------------------------------------

def _record(
    name: str,
    category: str,
    expected: Any,
    got: Any,
    passed: bool,
    detail: str = "",
) -> TestResult:
    r = TestResult(name=name, category=category, expected=expected, got=got,
                   passed=passed, detail=detail)
    _results.append(r)
    status = "PASS" if passed else "FAIL"
    _log(f"[{status}] {name} | expected={expected} got={got}"
         + (f" | {detail}" if detail else ""))
    return r


def _skip(name: str, category: str, reason: str) -> TestResult:
    r = TestResult(name=name, category=category, expected="—", got="—",
                   passed=True, skipped=True, skip_reason=reason)
    _results.append(r)
    _log(f"[SKIP] {name} | {reason}")
    return r


def _http(
    method: str,
    url: str,
    *,
    headers: Optional[Dict] = None,
    json: Optional[Any] = None,
    files: Optional[Any] = None,
    data: Optional[Any] = None,
    timeout: int = 15,
    allow_redirects: bool = True,
) -> requests.Response:
    fn = getattr(requests, method.lower())
    kwargs: Dict[str, Any] = {"timeout": timeout, "allow_redirects": allow_redirects}
    if headers:
        kwargs["headers"] = headers
    if json is not None:
        kwargs["json"] = json
    if files is not None:
        kwargs["files"] = files
    if data is not None:
        kwargs["data"] = data
    return fn(url, **kwargs)


# ---------------------------------------------------------------------------
# Test categories
# ---------------------------------------------------------------------------

# ── 1. Health & Auth ────────────────────────────────────────────────────────

def run_health_auth(base: str, keys: KeyStore) -> None:
    cat = "Health & Auth"

    # 1.1 Health check (no auth required)
    try:
        r = _http("GET", f"{base}/health")
        _record("GET /health → 200", cat, 200, r.status_code, r.status_code == 200)
        if r.status_code == 200:
            body = r.json()
            _record(
                "GET /health has status field", cat,
                "healthy", body.get("status"),
                body.get("status") == "healthy",
            )
            _log(f"  health body: {body}")
    except Exception as exc:
        _record("GET /health → 200", cat, 200, f"exception: {exc}", False, str(exc))
        # If health fails, the server is down – abort early
        print(f"\n  !! Server at {base} appears unreachable: {exc}")
        print("  !! Aborting remaining tests.\n")
        sys.exit(2)

    # 1.2 Root endpoint
    try:
        r = _http("GET", f"{base}/")
        _record("GET / → 200", cat, 200, r.status_code, r.status_code == 200)
    except Exception as exc:
        _record("GET / → 200", cat, 200, f"exception: {exc}", False, str(exc))

    # 1.3 Missing API key → 401 or 403
    try:
        r = _http("GET", f"{base}/towers")
        _record(
            "GET /towers (no key) → 401/403", cat,
            "401 or 403", r.status_code,
            r.status_code in (401, 403),
        )
    except Exception as exc:
        _record("GET /towers (no key) → 401/403", cat, "401 or 403",
                f"exception: {exc}", False, str(exc))

    # 1.4 Invalid API key → 401 or 403
    try:
        r = _http("GET", f"{base}/towers", headers={"X-API-Key": "totally-invalid-key-xyz"})
        _record(
            "GET /towers (bad key) → 401/403", cat,
            "401 or 403", r.status_code,
            r.status_code in (401, 403),
        )
    except Exception as exc:
        _record("GET /towers (bad key) → 401/403", cat, "401 or 403",
                f"exception: {exc}", False, str(exc))

    # 1.5 Valid free key → 200
    if keys.available("free"):
        try:
            r = _http("GET", f"{base}/towers", headers=keys.header("free"))
            _record(
                "GET /towers (free key) → 200", cat, 200, r.status_code,
                r.status_code == 200,
            )
        except Exception as exc:
            _record("GET /towers (free key) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /towers (free key) → 200", cat, "no free key available")

    # 1.6 Valid pro key → 200
    if keys.available("pro"):
        try:
            r = _http("GET", f"{base}/towers", headers=keys.header("pro"))
            _record(
                "GET /towers (pro key) → 200", cat, 200, r.status_code,
                r.status_code == 200,
            )
        except Exception as exc:
            _record("GET /towers (pro key) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /towers (pro key) → 200", cat, "no pro key available")

    # 1.7 Valid enterprise key → 200
    if keys.available("enterprise"):
        try:
            r = _http("GET", f"{base}/towers", headers=keys.header("enterprise"))
            _record(
                "GET /towers (enterprise key) → 200", cat, 200, r.status_code,
                r.status_code == 200,
            )
        except Exception as exc:
            _record("GET /towers (enterprise key) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /towers (enterprise key) → 200", cat, "no enterprise key available")

    # 1.8 X-RateLimit headers present on authenticated request
    if keys.available("pro"):
        try:
            r = _http("GET", f"{base}/towers", headers=keys.header("pro"))
            has_rl = "X-RateLimit-Remaining" in r.headers or "X-RateLimit-Limit" in r.headers
            _record(
                "Rate-limit headers present (pro)", cat,
                "X-RateLimit-* headers", "present" if has_rl else "absent",
                has_rl,
                detail=f"headers={dict(r.headers)}",
            )
        except Exception as exc:
            _record("Rate-limit headers present (pro)", cat, "X-RateLimit-* headers",
                    f"exception: {exc}", False, str(exc))


# ── 2. CRUD Operations ──────────────────────────────────────────────────────

def run_crud(base: str, keys: KeyStore, run_id: str) -> List[str]:
    """
    Run CRUD tests.  Returns a list of tower IDs created so the caller
    can clean them up even if a test fails mid-way.
    """
    cat = "CRUD"
    created_ids: List[str] = []

    if not keys.available("pro"):
        _skip("CRUD suite", cat, "no pro key available – skipping all CRUD tests")
        return created_ids

    h = keys.header("pro")
    tower_id = f"TEST_{run_id}_CRUD"

    # 2.1 Create tower
    new_tower = {
        "id": tower_id,
        "lat": -10.0,
        "lon": -50.0,
        "height_m": 30.0,
        "operator": "TestOp",
        "bands": ["700MHz"],
        "power_dbm": 40.0,
    }
    try:
        r = _http("POST", f"{base}/towers", headers=h, json=new_tower)
        passed = r.status_code in (200, 201)
        _record(f"POST /towers ({tower_id}) → 200/201", cat,
                "200 or 201", r.status_code, passed,
                detail=r.text[:200] if not passed else "")
        if passed:
            created_ids.append(tower_id)
    except Exception as exc:
        _record(f"POST /towers ({tower_id}) → 200/201", cat,
                "200 or 201", f"exception: {exc}", False, str(exc))
        return created_ids

    # 2.2 Read tower back
    try:
        r = _http("GET", f"{base}/towers/{tower_id}", headers=h)
        passed = r.status_code == 200
        _record(f"GET /towers/{tower_id} → 200", cat, 200, r.status_code, passed)
        if passed:
            body = r.json()
            _record(
                f"GET /towers/{tower_id} has correct lat", cat,
                -10.0, body.get("lat"), body.get("lat") == -10.0,
            )
    except Exception as exc:
        _record(f"GET /towers/{tower_id} → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 2.3 Update tower
    updated_tower = {**new_tower, "height_m": 60.0, "power_dbm": 46.0}
    try:
        r = _http("PUT", f"{base}/towers/{tower_id}", headers=h, json=updated_tower)
        passed = r.status_code == 200
        _record(f"PUT /towers/{tower_id} → 200", cat, 200, r.status_code, passed,
                detail=r.text[:200] if not passed else "")
    except Exception as exc:
        _record(f"PUT /towers/{tower_id} → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 2.4 Verify update persisted
    try:
        r = _http("GET", f"{base}/towers/{tower_id}", headers=h)
        if r.status_code == 200:
            body = r.json()
            _record(
                f"GET /towers/{tower_id} height_m updated to 60", cat,
                60.0, body.get("height_m"), body.get("height_m") == 60.0,
            )
            _record(
                f"GET /towers/{tower_id} power_dbm updated to 46", cat,
                46.0, body.get("power_dbm"), body.get("power_dbm") == 46.0,
            )
        else:
            _record(f"GET /towers/{tower_id} after update → 200", cat,
                    200, r.status_code, False)
    except Exception as exc:
        _record(f"GET /towers/{tower_id} after update → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 2.5 Delete tower
    try:
        r = _http("DELETE", f"{base}/towers/{tower_id}", headers=h)
        passed = r.status_code == 200
        _record(f"DELETE /towers/{tower_id} → 200", cat, 200, r.status_code, passed,
                detail=r.text[:200] if not passed else "")
        if passed and tower_id in created_ids:
            created_ids.remove(tower_id)
    except Exception as exc:
        _record(f"DELETE /towers/{tower_id} → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 2.6 Verify deletion (404)
    try:
        r = _http("GET", f"{base}/towers/{tower_id}", headers=h)
        _record(f"GET /towers/{tower_id} after delete → 404", cat,
                404, r.status_code, r.status_code == 404)
    except Exception as exc:
        _record(f"GET /towers/{tower_id} after delete → 404", cat, 404,
                f"exception: {exc}", False, str(exc))

    # 2.7 Delete non-existent tower → 404
    try:
        r = _http("DELETE", f"{base}/towers/NONEXISTENT_{run_id}", headers=h)
        _record("DELETE /towers/NONEXISTENT → 404", cat,
                404, r.status_code, r.status_code == 404)
    except Exception as exc:
        _record("DELETE /towers/NONEXISTENT → 404", cat, 404,
                f"exception: {exc}", False, str(exc))

    # 2.8 GET non-existent tower → 404
    try:
        r = _http("GET", f"{base}/towers/NONEXISTENT_{run_id}", headers=h)
        _record("GET /towers/NONEXISTENT → 404", cat,
                404, r.status_code, r.status_code == 404)
    except Exception as exc:
        _record("GET /towers/NONEXISTENT → 404", cat, 404,
                f"exception: {exc}", False, str(exc))

    # 2.9 PUT with mismatched ID → 400
    mismatch_tower = {**new_tower, "id": "DIFFERENT_ID"}
    try:
        r = _http("PUT", f"{base}/towers/{tower_id}", headers=h, json=mismatch_tower)
        _record("PUT /towers (mismatched ID) → 400", cat,
                400, r.status_code, r.status_code == 400)
    except Exception as exc:
        _record("PUT /towers (mismatched ID) → 400", cat, 400,
                f"exception: {exc}", False, str(exc))

    return created_ids


# ── 3. API Endpoints ────────────────────────────────────────────────────────

def run_api_endpoints(base: str, keys: KeyStore, run_id: str) -> List[str]:
    """
    Test list, filter, nearest, and analyze endpoints.
    Creates a known tower for deterministic tests, cleans up after.
    Returns list of created tower IDs for cleanup.
    """
    cat = "API Endpoints"
    created_ids: List[str] = []

    if not keys.available("pro"):
        _skip("API Endpoints suite", cat, "no pro key available")
        return created_ids

    h = keys.header("pro")
    tower_id = f"TEST_{run_id}_API"

    # Seed a known tower
    seed = {
        "id": tower_id,
        "lat": -23.56,
        "lon": -46.64,
        "height_m": 50.0,
        "operator": "Claro",
        "bands": ["1800MHz", "2100MHz"],
        "power_dbm": 43.0,
    }
    try:
        r = _http("POST", f"{base}/towers", headers=h, json=seed)
        if r.status_code in (200, 201):
            created_ids.append(tower_id)
            _log(f"[api] seeded tower {tower_id}")
        else:
            _skip("API Endpoints suite", cat,
                  f"could not seed test tower (HTTP {r.status_code})")
            return created_ids
    except Exception as exc:
        _skip("API Endpoints suite", cat, f"could not seed test tower: {exc}")
        return created_ids

    # 3.1 List towers
    try:
        r = _http("GET", f"{base}/towers", headers=h)
        passed = r.status_code == 200
        _record("GET /towers → 200", cat, 200, r.status_code, passed)
        if passed:
            body = r.json()
            has_towers = "towers" in body and isinstance(body["towers"], list)
            _record("GET /towers response has 'towers' list", cat,
                    True, has_towers, has_towers)
            _log(f"  tower count: {len(body.get('towers', []))}")
    except Exception as exc:
        _record("GET /towers → 200", cat, 200, f"exception: {exc}", False, str(exc))

    # 3.2 Filter by operator (match)
    try:
        r = _http("GET", f"{base}/towers?operator=Claro", headers=h)
        passed = r.status_code == 200
        _record("GET /towers?operator=Claro → 200", cat, 200, r.status_code, passed)
        if passed:
            towers = r.json().get("towers", [])
            all_claro = all(t.get("operator") == "Claro" for t in towers)
            _record(
                "GET /towers?operator=Claro all results are Claro", cat,
                True, all_claro, all_claro,
                detail=f"count={len(towers)}",
            )
    except Exception as exc:
        _record("GET /towers?operator=Claro → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 3.3 Filter by operator (no match)
    try:
        r = _http("GET", f"{base}/towers?operator=NOPE_XYZ_NOEXIST", headers=h)
        passed = r.status_code == 200
        _record("GET /towers?operator=NOPE → 200 (empty)", cat, 200, r.status_code, passed)
        if passed:
            towers = r.json().get("towers", [])
            _record(
                "GET /towers?operator=NOPE returns empty list", cat,
                0, len(towers), len(towers) == 0,
            )
    except Exception as exc:
        _record("GET /towers?operator=NOPE → 200 (empty)", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 3.4 Nearest towers
    try:
        r = _http("GET", f"{base}/towers/nearest?lat=-23.56&lon=-46.64&limit=3", headers=h)
        passed = r.status_code == 200
        _record("GET /towers/nearest → 200", cat, 200, r.status_code, passed)
        if passed:
            nearest = r.json().get("nearest_towers", [])
            _record(
                "GET /towers/nearest returns results", cat,
                True, len(nearest) > 0, len(nearest) > 0,
                detail=f"count={len(nearest)}",
            )
            if nearest:
                first = nearest[0]
                has_dist = "distance_km" in first or "distance" in first
                _record(
                    "GET /towers/nearest result has distance field", cat,
                    True, has_dist, has_dist,
                )
                _log(f"  nearest[0]: {first}")
    except Exception as exc:
        _record("GET /towers/nearest → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 3.5 Analyze link (success)
    try:
        body = {"lat": -23.57, "lon": -46.65, "height_m": 10.0, "antenna_gain_dbi": 12.0}
        r = _http("POST", f"{base}/analyze?tower_id={tower_id}", headers=h, json=body)
        passed = r.status_code == 200
        _record(f"POST /analyze (tower={tower_id}) → 200", cat, 200, r.status_code, passed,
                detail=r.text[:200] if not passed else "")
        if passed:
            a = r.json()
            _record(
                "POST /analyze response has 'feasible' field", cat,
                True, "feasible" in a, "feasible" in a,
            )
            _record(
                "POST /analyze response has 'signal_dbm' field", cat,
                True, "signal_dbm" in a, "signal_dbm" in a,
            )
            _log(f"  analyze result: feasible={a.get('feasible')} signal={a.get('signal_dbm')}")
    except Exception as exc:
        _record(f"POST /analyze (tower={tower_id}) → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 3.6 Analyze link (tower not found → 404)
    try:
        body = {"lat": -23.57, "lon": -46.65, "height_m": 10.0, "antenna_gain_dbi": 12.0}
        r = _http("POST", f"{base}/analyze?tower_id=NOEXIST_{run_id}", headers=h, json=body)
        _record("POST /analyze (unknown tower) → 404", cat,
                404, r.status_code, r.status_code == 404)
    except Exception as exc:
        _record("POST /analyze (unknown tower) → 404", cat, 404,
                f"exception: {exc}", False, str(exc))

    # 3.7 Pagination: limit and offset
    try:
        r = _http("GET", f"{base}/towers?limit=1&offset=0", headers=h)
        passed = r.status_code == 200
        _record("GET /towers?limit=1 → 200", cat, 200, r.status_code, passed)
        if passed:
            towers = r.json().get("towers", [])
            _record(
                "GET /towers?limit=1 returns at most 1 tower", cat,
                True, len(towers) <= 1, len(towers) <= 1,
                detail=f"got {len(towers)}",
            )
    except Exception as exc:
        _record("GET /towers?limit=1 → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 3.8 Metrics endpoint (no auth)
    try:
        r = _http("GET", f"{base}/metrics")
        passed = r.status_code == 200
        _record("GET /metrics → 200", cat, 200, r.status_code, passed)
        if passed:
            has_requests = "http_requests_total" in r.text
            _record(
                "GET /metrics contains http_requests_total", cat,
                True, has_requests, has_requests,
            )
    except Exception as exc:
        _record("GET /metrics → 200", cat, 200, f"exception: {exc}", False, str(exc))

    return created_ids


# ── 4. Tier-gating Validation ───────────────────────────────────────────────

def run_tier_gating(base: str, keys: KeyStore, run_id: str) -> List[str]:
    """
    Validate that tier-gated endpoints enforce access correctly.
    Returns list of created tower IDs for cleanup.
    """
    cat = "Tier-gating"
    created_ids: List[str] = []

    # We need a real tower for batch/analyze tests
    tower_id = f"TEST_{run_id}_TIER"
    seed_key = (
        keys.header("pro") if keys.available("pro")
        else keys.header("enterprise") if keys.available("enterprise")
        else keys.header("free")
    )

    if not any(keys.available(t) for t in ("free", "pro", "enterprise")):
        _skip("Tier-gating suite", cat, "no keys available")
        return created_ids

    seed = {
        "id": tower_id,
        "lat": -15.79,
        "lon": -47.88,
        "height_m": 40.0,
        "operator": "Vivo",
        "bands": ["2100MHz"],
        "power_dbm": 43.0,
    }
    try:
        r = _http("POST", f"{base}/towers", headers=seed_key, json=seed)
        if r.status_code in (200, 201):
            created_ids.append(tower_id)
        else:
            _skip("Tier-gating suite", cat,
                  f"could not seed test tower (HTTP {r.status_code})")
            return created_ids
    except Exception as exc:
        _skip("Tier-gating suite", cat, f"could not seed test tower: {exc}")
        return created_ids

    # ── 4a. batch_reports endpoint ──────────────────────────────────────────

    # Minimal valid CSV (2 rows)
    _small_csv = "lat,lon\n-23.56,-46.64\n-22.90,-43.17\n"

    # Free tier → 403
    if keys.available("free"):
        try:
            files = {"csv_file": ("test.csv", io.BytesIO(_small_csv.encode()), "text/csv")}
            r = _http(
                "POST",
                f"{base}/batch_reports?tower_id={tower_id}",
                headers=keys.header("free"),
                files=files,
            )
            _record(
                "POST /batch_reports (free tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("POST /batch_reports (free tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("POST /batch_reports (free tier) → 403", cat, "no free key available")

    # Pro tier → 200 (small batch, sync)
    if keys.available("pro"):
        try:
            files = {"csv_file": ("test.csv", io.BytesIO(_small_csv.encode()), "text/csv")}
            r = _http(
                "POST",
                f"{base}/batch_reports?tower_id={tower_id}",
                headers=keys.header("pro"),
                files=files,
                timeout=60,
            )
            passed = r.status_code == 200
            _record(
                "POST /batch_reports (pro tier, small) → 200", cat,
                200, r.status_code, passed,
                detail=r.text[:200] if not passed else "",
            )
        except Exception as exc:
            _record("POST /batch_reports (pro tier, small) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("POST /batch_reports (pro tier, small) → 200", cat, "no pro key available")

    # Enterprise tier → 200 (small batch, sync)
    if keys.available("enterprise"):
        try:
            files = {"csv_file": ("test.csv", io.BytesIO(_small_csv.encode()), "text/csv")}
            r = _http(
                "POST",
                f"{base}/batch_reports?tower_id={tower_id}",
                headers=keys.header("enterprise"),
                files=files,
                timeout=60,
            )
            passed = r.status_code == 200
            _record(
                "POST /batch_reports (enterprise tier, small) → 200", cat,
                200, r.status_code, passed,
                detail=r.text[:200] if not passed else "",
            )
        except Exception as exc:
            _record("POST /batch_reports (enterprise tier, small) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("POST /batch_reports (enterprise tier, small) → 200", cat,
              "no enterprise key available")

    # Pro tier: batch row limit (2k rows → should succeed or queue)
    if keys.available("pro"):
        _test_batch_row_limit(base, keys, tower_id, cat, tier="pro",
                              limit=BATCH_LIMIT_PRO)

    # Enterprise tier: batch row limit (10k rows → should succeed or queue)
    if keys.available("enterprise"):
        _test_batch_row_limit(base, keys, tower_id, cat, tier="enterprise",
                              limit=BATCH_LIMIT_ENTERPRISE)

    # ── 4b. export_report endpoint ──────────────────────────────────────────

    # Free tier → 403
    if keys.available("free"):
        try:
            r = _http(
                "GET",
                f"{base}/export_report?tower_id={tower_id}&lat=-23.57&lon=-46.65",
                headers=keys.header("free"),
            )
            _record(
                "GET /export_report (free tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("GET /export_report (free tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /export_report (free tier) → 403", cat, "no free key available")

    # Pro tier → 200 (PDF)
    if keys.available("pro"):
        try:
            r = _http(
                "GET",
                f"{base}/export_report?tower_id={tower_id}&lat=-23.57&lon=-46.65",
                headers=keys.header("pro"),
                timeout=30,
            )
            passed = r.status_code == 200
            _record(
                "GET /export_report (pro tier) → 200", cat,
                200, r.status_code, passed,
                detail=r.text[:200] if not passed else "",
            )
            if passed:
                is_pdf = r.headers.get("content-type", "").startswith("application/pdf")
                _record(
                    "GET /export_report (pro) content-type is PDF", cat,
                    True, is_pdf, is_pdf,
                )
        except Exception as exc:
            _record("GET /export_report (pro tier) → 200", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /export_report (pro tier) → 200", cat, "no pro key available")

    # ── 4c. Bedrock endpoints ───────────────────────────────────────────────

    # Free tier → 403 on /bedrock/chat
    if keys.available("free"):
        try:
            r = _http(
                "POST",
                f"{base}/bedrock/chat",
                headers=keys.header("free"),
                json={"prompt": "What is a Fresnel zone?"},
            )
            _record(
                "POST /bedrock/chat (free tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("POST /bedrock/chat (free tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("POST /bedrock/chat (free tier) → 403", cat, "no free key available")

    # Free tier → 403 on /bedrock/models
    if keys.available("free"):
        try:
            r = _http("GET", f"{base}/bedrock/models", headers=keys.header("free"))
            _record(
                "GET /bedrock/models (free tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("GET /bedrock/models (free tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /bedrock/models (free tier) → 403", cat, "no free key available")

    # Pro tier → 200 or 502 on /bedrock/chat
    # (502 is acceptable when AWS credentials are not configured in the test env)
    if keys.available("pro"):
        try:
            r = _http(
                "POST",
                f"{base}/bedrock/chat",
                headers=keys.header("pro"),
                json={"prompt": "What is a Fresnel zone?"},
                timeout=30,
            )
            passed = r.status_code in (200, 502)
            _record(
                "POST /bedrock/chat (pro tier) → 200 or 502", cat,
                "200 or 502", r.status_code, passed,
                detail=r.text[:200] if not passed else "",
            )
        except Exception as exc:
            _record("POST /bedrock/chat (pro tier) → 200 or 502", cat,
                    "200 or 502", f"exception: {exc}", False, str(exc))
    else:
        _skip("POST /bedrock/chat (pro tier) → 200 or 502", cat, "no pro key available")

    # ── 4d. SRTM endpoints (enterprise only) ───────────────────────────────

    # Free tier → 403 on /srtm/status/BR
    if keys.available("free"):
        try:
            r = _http("GET", f"{base}/srtm/status/BR", headers=keys.header("free"))
            _record(
                "GET /srtm/status/BR (free tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("GET /srtm/status/BR (free tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /srtm/status/BR (free tier) → 403", cat, "no free key available")

    # Pro tier → 403 on /srtm/status/BR
    if keys.available("pro"):
        try:
            r = _http("GET", f"{base}/srtm/status/BR", headers=keys.header("pro"))
            _record(
                "GET /srtm/status/BR (pro tier) → 403", cat,
                403, r.status_code, r.status_code == 403,
                detail=r.text[:200] if r.status_code != 403 else "",
            )
        except Exception as exc:
            _record("GET /srtm/status/BR (pro tier) → 403", cat, 403,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /srtm/status/BR (pro tier) → 403", cat, "no pro key available")

    # Enterprise tier → 200 or 404 on /srtm/status/BR
    # (404 is acceptable if the country code is not in COUNTRY_BOUNDS)
    if keys.available("enterprise"):
        try:
            r = _http("GET", f"{base}/srtm/status/BR", headers=keys.header("enterprise"))
            passed = r.status_code in (200, 404)
            _record(
                "GET /srtm/status/BR (enterprise tier) → 200/404", cat,
                "200 or 404", r.status_code, passed,
                detail=r.text[:200] if not passed else "",
            )
        except Exception as exc:
            _record("GET /srtm/status/BR (enterprise tier) → 200/404", cat,
                    "200 or 404", f"exception: {exc}", False, str(exc))
    else:
        _skip("GET /srtm/status/BR (enterprise tier) → 200/404", cat,
              "no enterprise key available")

    return created_ids


def _test_batch_row_limit(
    base: str,
    keys: KeyStore,
    tower_id: str,
    cat: str,
    tier: str,
    limit: int,
) -> None:
    """
    Submit a CSV with exactly `limit` rows and verify the server accepts it
    (200 sync or 200 queued).  Then submit `limit + 1` rows and verify 400.
    """
    h = keys.header(tier)

    # Build a CSV with exactly `limit` rows
    rows = ["lat,lon"] + [f"-23.{i % 100:02d},-46.{i % 100:02d}" for i in range(limit)]
    csv_at_limit = "\n".join(rows) + "\n"

    try:
        files = {"csv_file": ("test.csv", io.BytesIO(csv_at_limit.encode()), "text/csv")}
        r = _http(
            "POST",
            f"{base}/batch_reports?tower_id={tower_id}",
            headers=h,
            files=files,
            timeout=30,
        )
        # Accepted: 200 (sync result or queued job)
        passed = r.status_code == 200
        _record(
            f"POST /batch_reports ({tier}, {limit} rows) → 200", cat,
            200, r.status_code, passed,
            detail=r.text[:200] if not passed else "",
        )
    except Exception as exc:
        _record(f"POST /batch_reports ({tier}, {limit} rows) → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # Build a CSV with `limit + 1` rows (should be rejected with 400)
    rows_over = ["lat,lon"] + [f"-23.{i % 100:02d},-46.{i % 100:02d}" for i in range(limit + 1)]
    csv_over_limit = "\n".join(rows_over) + "\n"

    try:
        files = {"csv_file": ("test.csv", io.BytesIO(csv_over_limit.encode()), "text/csv")}
        r = _http(
            "POST",
            f"{base}/batch_reports?tower_id={tower_id}",
            headers=h,
            files=files,
            timeout=30,
        )
        _record(
            f"POST /batch_reports ({tier}, {limit + 1} rows) → 400", cat,
            400, r.status_code, r.status_code == 400,
            detail=r.text[:200] if r.status_code != 400 else "",
        )
    except Exception as exc:
        _record(f"POST /batch_reports ({tier}, {limit + 1} rows) → 400", cat, 400,
                f"exception: {exc}", False, str(exc))


# ── 5. Rate Limiting ────────────────────────────────────────────────────────

def run_rate_limiting(base: str, keys: KeyStore) -> None:
    """
    Verify that the free tier hits 429 after exhausting its per-minute limit.
    The free tier default is 10 req/min (RATE_LIMIT_FREE env var).

    NOTE: This test fires rapid requests and may temporarily exhaust the
    free key's rate limit for the remainder of the minute.  It is skipped
    if no free key is available.
    """
    cat = "Rate Limiting"

    if not keys.available("free"):
        _skip("Rate limit: free tier 429 after limit", cat, "no free key available")
        _skip("Rate limit: pro tier higher limit than free", cat, "no pro key available")
        return

    free_h = keys.header("free")
    # Default free limit is 10 req/min.  Fire 15 rapid requests and expect at
    # least one 429.  We stop as soon as we see one to avoid hammering the server.
    hit_429 = False
    responses: List[int] = []
    for _ in range(15):
        try:
            r = _http("GET", f"{base}/towers", headers=free_h, timeout=5)
            responses.append(r.status_code)
            if r.status_code == 429:
                hit_429 = True
                break
        except Exception:
            break

    _record(
        "Free tier hits 429 after rate limit", cat,
        True, hit_429, hit_429,
        detail=f"responses={responses}",
    )

    # Pro tier should have a higher limit (100 req/min default).
    # We just verify it doesn't 429 on a single request after the free key
    # may have been exhausted.
    if keys.available("pro"):
        try:
            r = _http("GET", f"{base}/towers", headers=keys.header("pro"), timeout=5)
            _record(
                "Pro tier not rate-limited on single request", cat,
                200, r.status_code, r.status_code == 200,
            )
        except Exception as exc:
            _record("Pro tier not rate-limited on single request", cat, 200,
                    f"exception: {exc}", False, str(exc))
    else:
        _skip("Pro tier not rate-limited on single request", cat, "no pro key available")


# ── 6. Signup Endpoints ─────────────────────────────────────────────────────

def run_signup(base: str, run_id: str) -> None:
    cat = "Signup"

    # 6.1 Register a new free user
    email = f"test-signup-{run_id}@test.invalid"
    try:
        r = _http("POST", f"{base}/signup/free", json={"email": email}, timeout=10)
        passed = r.status_code == 201
        _record("POST /signup/free (new email) → 201", cat, 201, r.status_code, passed,
                detail=r.text[:200] if not passed else "")
        if passed:
            body = r.json()
            has_key = "api_key" in body
            has_tier = body.get("tier") == "free"
            _record("POST /signup/free response has api_key", cat, True, has_key, has_key)
            _record("POST /signup/free tier is 'free'", cat, "free", body.get("tier"), has_tier)
    except Exception as exc:
        _record("POST /signup/free (new email) → 201", cat, 201,
                f"exception: {exc}", False, str(exc))

    # 6.2 Duplicate email → 409
    try:
        r = _http("POST", f"{base}/signup/free", json={"email": email}, timeout=10)
        _record("POST /signup/free (duplicate email) → 409", cat,
                409, r.status_code, r.status_code == 409,
                detail=r.text[:200] if r.status_code != 409 else "")
    except Exception as exc:
        _record("POST /signup/free (duplicate email) → 409", cat, 409,
                f"exception: {exc}", False, str(exc))

    # 6.3 Invalid email (too short) → 422
    try:
        r = _http("POST", f"{base}/signup/free", json={"email": "x"}, timeout=10)
        _record("POST /signup/free (invalid email) → 422", cat,
                422, r.status_code, r.status_code == 422)
    except Exception as exc:
        _record("POST /signup/free (invalid email) → 422", cat, 422,
                f"exception: {exc}", False, str(exc))

    # 6.4 Status lookup for registered email
    try:
        r = _http("POST", f"{base}/signup/status", json={"email": email}, timeout=10)
        passed = r.status_code == 200
        _record("POST /signup/status (known email) → 200", cat, 200, r.status_code, passed,
                detail=r.text[:200] if not passed else "")
        if passed:
            body = r.json()
            _record("POST /signup/status has api_key field", cat,
                    True, "api_key" in body, "api_key" in body)
    except Exception as exc:
        _record("POST /signup/status (known email) → 200", cat, 200,
                f"exception: {exc}", False, str(exc))

    # 6.5 Status lookup for unknown email → 404
    try:
        r = _http("POST", f"{base}/signup/status",
                  json={"email": f"unknown-{run_id}@test.invalid"}, timeout=10)
        _record("POST /signup/status (unknown email) → 404", cat,
                404, r.status_code, r.status_code == 404)
    except Exception as exc:
        _record("POST /signup/status (unknown email) → 404", cat, 404,
                f"exception: {exc}", False, str(exc))

    # 6.6 Checkout – 200 if Stripe is configured, 400/503 otherwise
    try:
        r = _http(
            "POST",
            f"{base}/signup/checkout",
            json={"email": email, "tier": "pro"},
            timeout=10,
        )
        passed = r.status_code in (200, 400, 503)
        _record(
            "POST /signup/checkout → 200/400/503", cat,
            "200, 400 or 503", r.status_code, passed,
            detail=r.text[:200] if not passed else "",
        )
    except Exception as exc:
        _record("POST /signup/checkout → 200/400/503", cat,
                "200, 400 or 503", f"exception: {exc}", False, str(exc))


# ── 7. Security / Edge Cases ────────────────────────────────────────────────

def run_security(base: str, keys: KeyStore) -> None:
    cat = "Security"

    h = keys.header("pro") if keys.available("pro") else {}

    # 7.1 Path traversal on /towers/{id} → 404 (not 500)
    try:
        r = _http("GET", f"{base}/towers/..%2F..%2Fetc%2Fpasswd", headers=h)
        passed = r.status_code in (400, 404, 422)
        _record(
            "GET /towers (path traversal) → 400/404/422", cat,
            "400/404/422", r.status_code, passed,
        )
    except Exception as exc:
        _record("GET /towers (path traversal) → 400/404/422", cat,
                "400/404/422", f"exception: {exc}", False, str(exc))

    # 7.2 Oversized request body → 413
    if h:
        try:
            big_payload = {"id": "X", "lat": 0.0, "lon": 0.0, "height_m": 10.0,
                           "operator": "X", "bands": ["700MHz"],
                           "junk": "A" * (11 * 1024 * 1024)}
            r = _http("POST", f"{base}/towers", headers=h, json=big_payload, timeout=10)
            passed = r.status_code in (400, 413, 422)
            _record(
                "POST /towers (oversized body) → 400/413/422", cat,
                "400/413/422", r.status_code, passed,
            )
        except Exception as exc:
            # Connection reset / timeout is also acceptable for oversized payloads
            _record("POST /towers (oversized body) → 400/413/422", cat,
                    "400/413/422", f"exception: {exc}", True,
                    detail="connection error on oversized payload is acceptable")

    # 7.3 Security headers present
    try:
        r = _http("GET", f"{base}/health")
        headers = r.headers
        for hdr in ("X-Content-Type-Options", "X-Frame-Options"):
            present = hdr in headers
            _record(
                f"Security header '{hdr}' present", cat,
                True, present, present,
            )
    except Exception as exc:
        _record("Security headers present", cat, True,
                f"exception: {exc}", False, str(exc))

    # 7.4 CORS headers on OPTIONS preflight
    try:
        # Use an origin appropriate for the environment
        if "localhost" in base:
            cors_origin = "http://localhost:3000"
        else:
            cors_origin = "https://app.telecomtowerpower.com.br"
        r = requests.options(
            f"{base}/towers",
            headers={
                "Origin": cors_origin,
                "Access-Control-Request-Method": "GET",
            },
            timeout=10,
        )
        has_cors = "Access-Control-Allow-Origin" in r.headers
        _record(
            "OPTIONS /towers returns CORS headers", cat,
            True, has_cors, has_cors,
            detail=f"status={r.status_code}",
        )
    except Exception as exc:
        _record("OPTIONS /towers returns CORS headers", cat, True,
                f"exception: {exc}", False, str(exc))


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_towers(base: str, keys: KeyStore, tower_ids: List[str]) -> None:
    """Delete all test towers created during the run."""
    if not tower_ids:
        return

    h = (
        keys.header("pro") if keys.available("pro")
        else keys.header("enterprise") if keys.available("enterprise")
        else keys.header("free")
    )
    if not h:
        _log("[cleanup] no key available for cleanup")
        return

    for tid in tower_ids:
        try:
            r = _http("DELETE", f"{base}/towers/{tid}", headers=h, timeout=10)
            _log(f"[cleanup] DELETE /towers/{tid} → {r.status_code}")
        except Exception as exc:
            _log(f"[cleanup] DELETE /towers/{tid} failed: {exc}")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_CATEGORY_ORDER = [
    "Health & Auth",
    "Signup",
    "CRUD",
    "API Endpoints",
    "Tier-gating",
    "Rate Limiting",
    "Security",
]

_COL_NAME     = 55
_COL_EXPECTED = 14
_COL_GOT      = 14
_COL_STATUS   =  6


def _print_summary(env: str, base_url: str, elapsed: float) -> bool:
    """Print the results table and return True if all non-skipped tests passed."""
    total = len(_results)
    skipped = sum(1 for r in _results if r.skipped)
    passed  = sum(1 for r in _results if not r.skipped and r.passed)
    failed  = sum(1 for r in _results if not r.skipped and not r.passed)

    # Group by category
    by_cat: Dict[str, List[TestResult]] = {}
    for r in _results:
        by_cat.setdefault(r.category, []).append(r)

    sep = "─" * (_COL_NAME + _COL_EXPECTED + _COL_GOT + _COL_STATUS + 9)

    print()
    print("╔" + "═" * (len(sep) - 2) + "╗")
    title = f"  TELECOM TOWER POWER – Test Suite Results  [{env.upper()}]  {base_url}"
    print(f"║{title:<{len(sep) - 2}}║")
    print("╚" + "═" * (len(sep) - 2) + "╝")
    print()

    header = (
        f"{'Test':<{_COL_NAME}}  "
        f"{'Expected':>{_COL_EXPECTED}}  "
        f"{'Got':>{_COL_GOT}}  "
        f"{'':>{_COL_STATUS}}"
    )
    print(header)
    print(sep)

    for cat in _CATEGORY_ORDER:
        results_in_cat = by_cat.get(cat, [])
        if not results_in_cat:
            continue
        print(f"\n  ── {cat} ──")
        for r in results_in_cat:
            if r.skipped:
                status_str = "SKIP"
                exp_str = "—"
                got_str = "—"
            else:
                status_str = "PASS" if r.passed else "FAIL"
                exp_str = str(r.expected)[:_COL_EXPECTED]
                got_str = str(r.got)[:_COL_GOT]

            line = (
                f"  {r.name:<{_COL_NAME - 2}}  "
                f"{exp_str:>{_COL_EXPECTED}}  "
                f"{got_str:>{_COL_GOT}}  "
                f"{status_str:>{_COL_STATUS}}"
            )
            print(line)
            if not r.passed and not r.skipped and r.detail:
                print(f"    {'':>{_COL_NAME - 2}}  detail: {r.detail}")
            if r.skipped and r.skip_reason:
                print(f"    {'':>{_COL_NAME - 2}}  reason: {r.skip_reason}")

    print()
    print(sep)
    print(
        f"  Total: {total}  |  "
        f"Passed: {passed}  |  "
        f"Failed: {failed}  |  "
        f"Skipped: {skipped}  |  "
        f"Elapsed: {elapsed:.1f}s"
    )
    print(sep)

    if failed == 0:
        print("\n  ✓  ALL TESTS PASSED\n")
    else:
        print(f"\n  ✗  {failed} TEST(S) FAILED\n")

    return failed == 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _verbose

    parser = argparse.ArgumentParser(
        description="TELECOM TOWER POWER – comprehensive API test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--env",
        choices=list(ENV_URLS.keys()),
        default="local",
        help="Target environment (default: local)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override base URL (e.g. http://localhost:8001)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed per-test output",
    )
    parser.add_argument(
        "--no-demo-keys",
        action="store_true",
        help="Skip demo key validation; only use dynamically registered keys",
    )
    parser.add_argument(
        "--free-key",
        default=None,
        help="Provide an explicit free-tier API key",
    )
    parser.add_argument(
        "--pro-key",
        default=None,
        help="Provide an explicit pro-tier API key",
    )
    parser.add_argument(
        "--enterprise-key",
        default=None,
        help="Provide an explicit enterprise-tier API key",
    )
    args = parser.parse_args()

    _verbose = args.verbose
    env = args.env
    base = args.base_url or ENV_URLS[env]
    run_id = uuid.uuid4().hex[:8]

    print(f"\n  TELECOM TOWER POWER – Test Suite")
    print(f"  Environment : {env}")
    print(f"  Base URL    : {base}")
    print(f"  Run ID      : {run_id}")
    print(f"  Verbose     : {_verbose}")
    print()

    # ── Key provisioning ────────────────────────────────────────────────────
    keys = KeyStore()

    # Inject explicitly provided keys first
    if args.free_key:
        keys.keys["free"] = args.free_key
        _log(f"[keys] free: using --free-key {args.free_key!r}")
    if args.pro_key:
        keys.keys["pro"] = args.pro_key
        _log(f"[keys] pro: using --pro-key {args.pro_key!r}")
    if args.enterprise_key:
        keys.keys["enterprise"] = args.enterprise_key
        _log(f"[keys] enterprise: using --enterprise-key {args.enterprise_key!r}")

    # Fill in any missing keys via demo keys / /signup/free
    use_demo = not args.no_demo_keys
    keys.provision(base, use_demo=use_demo)

    print(f"  Keys        : free={'✓' if keys.available('free') else '✗'}  "
          f"pro={'✓' if keys.available('pro') else '✗'}  "
          f"enterprise={'✓' if keys.available('enterprise') else '✗'}")
    print()

    # ── Run tests ───────────────────────────────────────────────────────────
    t_start = time.monotonic()
    all_created_ids: List[str] = []

    try:
        run_health_auth(base, keys)
        run_signup(base, run_id)
        all_created_ids += run_crud(base, keys, run_id)
        all_created_ids += run_api_endpoints(base, keys, run_id)
        all_created_ids += run_tier_gating(base, keys, run_id)
        run_rate_limiting(base, keys)
        run_security(base, keys)
    finally:
        # Always attempt cleanup
        cleanup_towers(base, keys, all_created_ids)

    elapsed = time.monotonic() - t_start

    # ── Print summary ────────────────────────────────────────────────────────
    all_passed = _print_summary(env, base, elapsed)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
