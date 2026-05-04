# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Drive-test CSV → /coverage/observations/batch uploader.

Phase 1 ingest path for the Q3/2026 real-data milestone (issue #30).

Reads a scanner-exported CSV, joins per-cell tower metadata from a JSON
sidecar, and POSTs in chunks to the batch endpoint with strict
calibration fields populated explicitly so the drive-test validator
(commit a64c014) accepts the rows.

The script REFUSES to upload until every row has all four calibration
fields (``tx_gain_dbi``, ``rx_gain_dbi``, ``cable_loss_db``,
``rx_height_m``) populated to a non-default value, so a missing
calibration step on the field side cannot silently fall through to
the synthetic-friendly defaults.

Usage::

    python scripts/drivetest_to_observations.py \\
        --csv scan.csv \\
        --tower-meta tower_meta.json \\
        --source drivetest_pilot \\
        --api https://api.telecomtowerpower.com.br \\
        --api-key "$TTP_DRIVETEST_KEY" \\
        --batch-size 500 \\
        [--dry-run]

CSV column expectations (case-insensitive):

    timestamp, lat, lon, tower_id, band_mhz | freq_hz,
    rssi_dbm | rsrp_dbm | observed_dbm

tower_meta.json shape::

    {
      "<tower_id>": {
        "tx_lat": -23.5, "tx_lon": -46.6,
        "tx_height_m": 32.0,
        "tx_power_dbm": 43.0,
        "tx_gain_dbi": 16.5,
        "freq_hz": 2600000000  // optional fallback if CSV has no band
      },
      ...
    }

Per-vehicle calibration sidecar (``--rx-calibration calibration.json``)::

    {
      "rx_gain_dbi": 4.2,
      "rx_height_m": 1.74,
      "cable_loss_db": {
        "700":  2.8,
        "1800": 3.1,
        "2600": 3.6
      }
    }
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from typing import Any, Dict, Iterator, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: 'requests' not installed (pip install requests)", file=sys.stderr)
    sys.exit(2)


_OBS_COLS_RX = ("rx_lat", "rx_lon", "rx_height_m", "rx_gain_dbi", "cable_loss_db")
_OBS_COLS_TX = ("tx_lat", "tx_lon", "tx_height_m", "tx_power_dbm", "tx_gain_dbi", "freq_hz")
_REQUIRED_DRIVETEST = ("tx_gain_dbi", "rx_gain_dbi", "cable_loss_db", "rx_height_m")

_LAT_ALIASES = ("lat", "latitude", "rx_lat", "GPS Latitude")
_LON_ALIASES = ("lon", "lng", "long", "longitude", "rx_lon", "GPS Longitude")
_RSSI_ALIASES = (
    "rssi_dbm", "observed_dbm", "rsrp_dbm", "rsrp", "rscp", "rxlev",
    "RSRP", "Best Signal Level [dBm]",
)
_BAND_ALIASES = ("band_mhz", "band", "frequency_mhz", "freq_mhz")
_FREQ_HZ_ALIASES = ("freq_hz", "frequency_hz")
_TS_ALIASES = ("timestamp", "ts", "time", "datetime")
_TOWER_ALIASES = ("tower_id", "cell_id", "site_id")


def _pick(row: Dict[str, str], aliases: tuple) -> Optional[str]:
    lower = {k.lower(): v for k, v in row.items()}
    for a in aliases:
        if a.lower() in lower and lower[a.lower()].strip() != "":
            return lower[a.lower()]
    return None


def _band_to_hz(band_mhz: float) -> Optional[float]:
    """Map common cellular band number to centre frequency in Hz.

    The trainer needs ``freq_hz``; CSVs from TEMS / G-NetTrack often
    only carry the band designator. Returning ``None`` signals the
    caller to pull ``freq_hz`` from the tower metadata sidecar.
    """
    band = int(round(band_mhz))
    table = {
        700: 700e6, 800: 800e6, 850: 850e6, 900: 900e6,
        1700: 1700e6, 1800: 1800e6, 1900: 1900e6, 2100: 2100e6,
        2300: 2300e6, 2500: 2500e6, 2600: 2600e6,
        3500: 3500e6, 3600: 3600e6, 3700: 3700e6,
    }
    return table.get(band)


def _parse_ts(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return float(raw)  # already epoch seconds
    except ValueError:
        pass
    # ISO-8601
    try:
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _row_to_obs(
    row: Dict[str, str],
    tower_meta: Dict[str, Dict[str, Any]],
    rx_cal: Dict[str, Any],
    source: str,
) -> Optional[Dict[str, Any]]:
    """Normalise one CSV row into a /coverage/observations payload.

    Returns ``None`` for unrecoverable rows; raises for calibration
    sidecar errors that should abort the whole job (operator mistake
    we don't want to silently mask).
    """
    tower_id = _pick(row, _TOWER_ALIASES)
    if not tower_id or tower_id not in tower_meta:
        return None
    meta = tower_meta[tower_id]

    lat = _pick(row, _LAT_ALIASES)
    lon = _pick(row, _LON_ALIASES)
    rssi = _pick(row, _RSSI_ALIASES)
    if not (lat and lon and rssi):
        return None

    # Resolve freq_hz: explicit > band lookup > tower meta fallback
    f_hz_raw = _pick(row, _FREQ_HZ_ALIASES)
    if f_hz_raw:
        f_hz = float(f_hz_raw)
    else:
        band_raw = _pick(row, _BAND_ALIASES)
        f_hz = _band_to_hz(float(band_raw)) if band_raw else None
        if f_hz is None:
            f_hz = float(meta.get("freq_hz") or 0)
    if f_hz <= 0:
        return None

    # Resolve cable_loss_db per-band from the calibration sidecar.
    cable_map = rx_cal.get("cable_loss_db")
    if not isinstance(cable_map, dict) or not cable_map:
        raise ValueError(
            "rx-calibration JSON is missing a cable_loss_db map. "
            "Phase 1 cannot proceed without per-band rx-side calibration."
        )
    band_key = str(int(round(f_hz / 1e6)))
    if band_key not in cable_map:
        # Try nearest band within ±50 MHz to be forgiving on naming.
        closest = min(cable_map.keys(), key=lambda b: abs(int(b) - int(band_key)))
        if abs(int(closest) - int(band_key)) > 50:
            raise ValueError(
                f"No cable_loss_db calibration for band ≈{band_key} MHz "
                f"(have {sorted(cable_map.keys())}). Re-run calibration."
            )
        band_key = closest
    cable_loss_raw = cable_map[band_key]
    if cable_loss_raw is None:
        raise ValueError(
            f"cable_loss_db['{band_key}'] is null — measure it before "
            "uploading. The placeholder template ships with nulls on "
            "purpose so uncalibrated runs fail loud."
        )
    cable_loss = float(cable_loss_raw)

    rx_gain = rx_cal.get("rx_gain_dbi")
    rx_height = rx_cal.get("rx_height_m")
    if rx_gain is None or rx_height is None:
        raise ValueError(
            "rx-calibration JSON must define rx_gain_dbi and rx_height_m."
        )

    obs = {
        "tower_id": tower_id,
        "tx_lat": float(meta["tx_lat"]),
        "tx_lon": float(meta["tx_lon"]),
        "tx_height_m": float(meta["tx_height_m"]),
        "tx_power_dbm": float(meta["tx_power_dbm"]),
        "tx_gain_dbi": float(meta["tx_gain_dbi"]),
        "rx_lat": float(lat),
        "rx_lon": float(lon),
        "rx_height_m": float(rx_height),
        "rx_gain_dbi": float(rx_gain),
        "cable_loss_db": cable_loss,
        "freq_hz": f_hz,
        "observed_dbm": float(rssi),
        "source": source,
    }
    ts = _parse_ts(_pick(row, _TS_ALIASES))
    if ts is not None:
        obs["ts"] = ts
    return obs


def _validate_obs(obs: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for k in _REQUIRED_DRIVETEST:
        if obs.get(k) is None:
            errs.append(f"missing {k}")
    if not (-150.0 <= obs.get("observed_dbm", -999) <= 30.0):
        errs.append("observed_dbm out of range")
    if not (1e6 < obs.get("freq_hz", 0) <= 100e9):
        errs.append("freq_hz out of range")
    if not (0.0 <= obs.get("cable_loss_db", -1) <= 20.0):
        errs.append("cable_loss_db out of [0,20]")
    return errs


def _chunks(seq: List[Dict[str, Any]], n: int) -> Iterator[List[Dict[str, Any]]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="Drive-test scanner CSV")
    p.add_argument("--tower-meta", required=True, help="JSON map tower_id -> tx_*")
    p.add_argument("--rx-calibration", required=True, help="JSON with rx_gain_dbi, rx_height_m, cable_loss_db{band->dB}")
    p.add_argument("--source", default="drivetest_pilot", help="link_observations.source tag (must start with 'drivetest_')")
    p.add_argument("--api", default="https://api.telecomtowerpower.com.br")
    p.add_argument("--api-key", default=os.environ.get("TTP_DRIVETEST_KEY", ""))
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--dry-run", action="store_true", help="Validate + serialise, no POST")
    p.add_argument("--out", help="If set, write the JSON payloads (one per chunk) here")
    args = p.parse_args()

    if not args.source.startswith("drivetest_"):
        print(f"ERROR: --source must start with 'drivetest_' (got {args.source!r})", file=sys.stderr)
        return 2
    if not args.dry_run and not args.api_key:
        print("ERROR: API key missing (use --api-key or $TTP_DRIVETEST_KEY)", file=sys.stderr)
        return 2

    with open(args.tower_meta) as f:
        tower_meta = json.load(f)
    with open(args.rx_calibration) as f:
        rx_cal = json.load(f)

    rows: List[Dict[str, Any]] = []
    skipped = 0
    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            obs = _row_to_obs(raw, tower_meta, rx_cal, args.source)
            if obs is None:
                skipped += 1
                continue
            errs = _validate_obs(obs)
            if errs:
                print(f"  row dropped ({'; '.join(errs)}): tower={obs.get('tower_id')}", file=sys.stderr)
                skipped += 1
                continue
            rows.append(obs)

    print(f"Parsed {len(rows)} valid rows (skipped {skipped}) from {args.csv}", file=sys.stderr)
    if not rows:
        print("ERROR: nothing to upload", file=sys.stderr)
        return 1

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"observations": rows}, f, indent=2)
        print(f"Wrote {args.out}", file=sys.stderr)

    if args.dry_run:
        print("DRY-RUN: skipping POST", file=sys.stderr)
        return 0

    sess = requests.Session()
    sess.headers.update({"X-API-Key": args.api_key, "Content-Type": "application/json"})
    url = args.api.rstrip("/") + "/coverage/observations/batch"
    total = 0
    for i, batch in enumerate(_chunks(rows, args.batch_size), 1):
        resp = sess.post(url, json={"observations": batch}, timeout=120)
        if resp.status_code >= 300:
            print(f"FAIL chunk {i} ({len(batch)} rows): HTTP {resp.status_code} {resp.text[:300]}", file=sys.stderr)
            return 1
        body = resp.json()
        n = body.get("ingested", 0)
        total += n
        print(f"  chunk {i}: ingested {n}/{len(batch)}", file=sys.stderr)
        time.sleep(0.5)  # polite throttle vs. rate-limiter

    print(f"DONE: ingested {total} rows into {args.api} as source={args.source}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
