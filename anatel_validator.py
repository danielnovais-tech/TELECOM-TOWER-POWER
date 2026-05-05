# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
anatel_validator.py – Certified validation of ANATEL ERB filings.

ANATEL (Agência Nacional de Telecomunicações) requires every base station
("Estação Rádio Base", ERB) to be filed with location, operator, frequency,
EIRP and antenna metadata before it goes on the air. This module validates
those filings against a deterministic rule set and produces an HMAC-signed
certificate so the operator can later prove which filing rows the platform
considered compliant at a given timestamp.

The rule set is intentionally conservative — it surfaces obvious data-entry
mistakes (CNPJ checksum, coordinates outside the Brazilian bounding box,
unknown UF, frequencies outside any licensed band) and is **not** a
substitute for legal review of an actual ANATEL submission.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

CERT_VERSION = "1.0"

# Brazilian states (UF codes per IBGE).
_VALID_UFS: frozenset[str] = frozenset({
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
    "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
    "RO", "RR", "RS", "SC", "SE", "SP", "TO",
})

# Brazilian bounding box (loose — includes Atol das Rocas / Trindade).
_BR_BBOX = {"lat_min": -33.75, "lat_max": 5.27, "lon_min": -73.99, "lon_max": -28.85}

# Licensed ANATEL commercial cellular bands (MHz center, ± half-bandwidth).
# Source: ANATEL Resolução nº 711/2019 + subsequent 5G NR allocations.
_ANATEL_BANDS_MHZ: Tuple[Tuple[float, float], ...] = (
    (450, 30),    # SMP rural
    (700, 30),    # Band 28 (LTE 700)
    (850, 35),    # Band 5 (legacy CDMA / GSM 850)
    (900, 35),    # GSM 900
    (1800, 75),   # Band 3 (LTE 1800)
    (1900, 75),   # Band 2
    (2100, 60),   # Band 1 (UMTS / LTE 2100)
    (2300, 50),   # Band 40 (LTE TDD)
    (2500, 100),  # Band 7 / 41
    (3500, 200),  # NR n78 (5G mid-band)
    (26000, 750), # NR n258 (5G mmWave 26 GHz)
)

_OPERATOR_ALLOWLIST: frozenset[str] = frozenset({
    "Vivo", "TIM", "Claro", "Oi", "Algar", "Sercomtel", "Nextel",
    "Brisanet", "Unifique", "Iez!", "Giga Mais",
})

_STATION_ID_RE = re.compile(r"^\d{4,12}$")
_DIGITS_ONLY_RE = re.compile(r"\D+")

# HMAC-SHA256 key used to sign certificates. Loaded once at import time so
# rotating it requires a service restart (a fresh signature must always be
# tied to the key in effect at the moment of validation).
_KEY_FILE = "/run/secrets/anatel_cert_hmac_key"
_KEY_ENV = "ANATEL_CERT_HMAC_KEY"


def _load_signing_key() -> bytes:
    try:
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE, "rb") as fh:
                v = fh.read().strip()
                if v:
                    return v
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(_KEY_ENV, "").strip().encode("utf-8")


_SIGNING_KEY: bytes = _load_signing_key()


# ---------------------------------------------------------------------------
# Field-level validators
# ---------------------------------------------------------------------------


def validate_cnpj(value: str) -> bool:
    """Return True iff ``value`` is a structurally valid Brazilian CNPJ.

    Implements the standard mod-11 weighted checksum on the 14-digit
    representation. Rejects all-equal-digit strings (000…0, 111…1, …) which
    pass the math but are invalid by ANATEL convention.
    """
    if not value:
        return False
    digits = _DIGITS_ONLY_RE.sub("", str(value))
    if len(digits) != 14:
        return False
    if digits == digits[0] * 14:
        return False

    def _check(slice_digits: str, weights: List[int]) -> int:
        total = sum(int(d) * w for d, w in zip(slice_digits, weights))
        rem = total % 11
        return 0 if rem < 2 else 11 - rem

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6] + w1
    d1 = _check(digits[:12], w1)
    d2 = _check(digits[:12] + str(d1), w2)
    return digits[12:] == f"{d1}{d2}"


def _coerce_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_str(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _classify_freq(freq_mhz: float) -> Optional[str]:
    """Return the matched ANATEL band label or ``None`` when out of band."""
    for center, half in _ANATEL_BANDS_MHZ:
        if abs(freq_mhz - center) <= half:
            return f"{int(center)}MHz"
    return None


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------


# Severity codes:
#   ERROR — the filing is rejected; ANATEL would return it.
#   WARN  — the filing is accepted but flagged for human review.

_REQUIRED_FIELDS = ("station_id", "cnpj", "operator", "uf", "municipio",
                    "lat", "lon", "height_m", "power_dbm", "freq_mhz")


def validate_filing(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a single filing dict and return a result document.

    The result has shape::

        {
          "station_id": "...",
          "ok": bool,
          "issues": [{"code": "...", "severity": "error|warn", "field": "...",
                      "message": "..."}, ...],
          "normalised": {<canonicalised fields used for the certificate>}
        }
    """
    issues: List[Dict[str, str]] = []

    def _err(code: str, field: str, msg: str) -> None:
        issues.append({"code": code, "severity": "error", "field": field, "message": msg})

    def _warn(code: str, field: str, msg: str) -> None:
        issues.append({"code": code, "severity": "warn", "field": field, "message": msg})

    # Presence checks first — surface them all in one pass.
    for f in _REQUIRED_FIELDS:
        if record.get(f) in (None, ""):
            _err("ERR_FIELD_REQUIRED", f, f"field '{f}' is required")

    station_id = _coerce_str(record.get("station_id"))
    cnpj = _coerce_str(record.get("cnpj"))
    operator = _coerce_str(record.get("operator"))
    uf = _coerce_str(record.get("uf")).upper()
    municipio = _coerce_str(record.get("municipio"))
    lat = _coerce_float(record.get("lat"))
    lon = _coerce_float(record.get("lon"))
    height_m = _coerce_float(record.get("height_m"))
    power_dbm = _coerce_float(record.get("power_dbm"))
    freq_mhz = _coerce_float(record.get("freq_mhz"))

    if station_id and not _STATION_ID_RE.match(station_id):
        _err("ERR_STATION_ID_FORMAT", "station_id",
             "station_id must be 4–12 numeric digits")

    if cnpj and not validate_cnpj(cnpj):
        _err("ERR_CNPJ_INVALID", "cnpj", "CNPJ checksum failed")

    if operator and operator not in _OPERATOR_ALLOWLIST:
        _warn("WARN_OPERATOR_UNKNOWN", "operator",
              f"operator '{operator}' is not in the ANATEL allowlist")

    if uf and uf not in _VALID_UFS:
        _err("ERR_UF_UNKNOWN", "uf", f"UF '{uf}' is not a valid Brazilian state")

    if lat is not None and not (-90.0 <= lat <= 90.0):
        _err("ERR_LAT_RANGE", "lat", "latitude out of [-90, 90]")
    if lon is not None and not (-180.0 <= lon <= 180.0):
        _err("ERR_LON_RANGE", "lon", "longitude out of [-180, 180]")

    if (
        lat is not None and lon is not None
        and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
        and not (
            _BR_BBOX["lat_min"] <= lat <= _BR_BBOX["lat_max"]
            and _BR_BBOX["lon_min"] <= lon <= _BR_BBOX["lon_max"]
        )
    ):
        _err("ERR_OUT_OF_BR_BBOX", "lat,lon",
             "coordinates fall outside the Brazilian bounding box")

    if height_m is not None and not (1.0 <= height_m <= 200.0):
        _err("ERR_HEIGHT_RANGE", "height_m",
             "height must be between 1 m and 200 m")
    elif height_m is not None and height_m > 120.0:
        _warn("WARN_HEIGHT_AVIATION", "height_m",
              "height >120 m may require ICAO/DECEA aeronautical clearance")

    if power_dbm is not None and not (-10.0 <= power_dbm <= 70.0):
        _err("ERR_POWER_RANGE", "power_dbm",
             "power_dbm must be between -10 dBm and 70 dBm")

    band_label: Optional[str] = None
    if freq_mhz is not None:
        if freq_mhz <= 0:
            _err("ERR_FREQ_RANGE", "freq_mhz", "freq_mhz must be > 0")
        else:
            band_label = _classify_freq(freq_mhz)
            if band_label is None:
                _err("ERR_FREQ_NOT_LICENSED", "freq_mhz",
                     f"frequency {freq_mhz} MHz is not within any ANATEL "
                     "commercial cellular band")

    has_error = any(i["severity"] == "error" for i in issues)

    # Canonicalised view used as input to the HMAC certificate so that
    # cosmetic differences (whitespace, casing) don't change the signature.
    normalised = {
        "station_id": station_id,
        "cnpj": _DIGITS_ONLY_RE.sub("", cnpj) if cnpj else "",
        "operator": operator,
        "uf": uf,
        "municipio": municipio,
        "lat": round(lat, 6) if lat is not None else None,
        "lon": round(lon, 6) if lon is not None else None,
        "height_m": round(height_m, 2) if height_m is not None else None,
        "power_dbm": round(power_dbm, 2) if power_dbm is not None else None,
        "freq_mhz": round(freq_mhz, 3) if freq_mhz is not None else None,
        "band": band_label,
    }

    return {
        "station_id": station_id or None,
        "ok": not has_error,
        "issues": issues,
        "normalised": normalised,
    }


def validate_batch(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate a list of filings and return per-row results plus a summary."""
    if not isinstance(records, list):
        raise TypeError("records must be a list of dicts")
    results = [validate_filing(r if isinstance(r, dict) else {}) for r in records]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "warnings": sum(
            1 for r in results
            if any(i["severity"] == "warn" for i in r["issues"])
        ),
    }
    return {"results": results, "summary": summary}


# ---------------------------------------------------------------------------
# Certification (HMAC-SHA256 over canonical JSON)
# ---------------------------------------------------------------------------


def _canonical_json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def certify(payload: Dict[str, Any], *, issuer: str = "TELECOM-TOWER-POWER") -> Dict[str, Any]:
    """Wrap ``payload`` in a signed envelope.

    Returns ``{"version", "issuer", "issued_at", "sha256", "signature",
    "signed": bool}``. When the HMAC key is unset (dev / CI) ``signed`` is
    ``False`` and ``signature`` is ``""`` so the caller can still distinguish
    a real, verifiable certificate from a placeholder.
    """
    body = _canonical_json(payload)
    digest = hashlib.sha256(body).hexdigest()
    issued_at = datetime.now(timezone.utc).isoformat()
    if _SIGNING_KEY:
        sig = hmac.new(_SIGNING_KEY, body, hashlib.sha256).hexdigest()
        signed = True
    else:
        sig = ""
        signed = False
    return {
        "version": CERT_VERSION,
        "issuer": issuer,
        "issued_at": issued_at,
        "sha256": digest,
        "signature": sig,
        "signed": signed,
        "alg": "HMAC-SHA256" if signed else "none",
    }


def verify_certificate(payload: Dict[str, Any], certificate: Dict[str, Any]) -> bool:
    """Constant-time check that ``certificate`` was issued for ``payload``.

    Returns ``False`` when the HMAC key is unset (no signature to verify).
    """
    if not certificate or not certificate.get("signed"):
        return False
    body = _canonical_json(payload)
    if hashlib.sha256(body).hexdigest() != certificate.get("sha256"):
        return False
    if not _SIGNING_KEY:
        return False
    expected = hmac.new(_SIGNING_KEY, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, certificate.get("signature", ""))
