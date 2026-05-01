# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Bulk-load real RF measurements into ``link_observations``.

This is the **only** real-data path the coverage model can learn from
(see ``coverage_predict.train_model`` which up-weights real rows 3× over
the synthetic baseline). The OpenCelliD free-tier feed reports
``averageSignal=0`` for 100 % of Brazilian rows, so it is NOT a viable
label source — confirmed empirically 2026-04-30 with token
``pk.e560…``: 54 549 rows downloaded, all with averageSignal=0.

Use this script when you have a drive-test or crowdsourced measurement
CSV with at least:

  * receiver lat/lon
  * observed signal (dBm)
  * carrier frequency (Hz, MHz, or band name)
  * tower / site lat/lon  (or a tower_id we can look up)

Where to find compatible datasets (license caveats apply — verify before
ingesting into production):

  * **Anatel "Acompanhamento e Controle"** drive-test reports — public,
    PT-BR, downloadable from gov.br/anatel. Best Brazilian source.
  * **Kaggle "LTE Drive Test" datasets** — Vasileios Kalantzis, etc.
    (auth required; free tier is fine).
  * **IEEE Dataport "5G/LTE drive test" collections** — most are
    CC-BY-NC, unsuitable for commercial production training.
  * **Own measurements** — `/coverage/observations` lets a customer
    submit one row at a time; pipe a recorded session through this
    script for bulk historical backfill.

Usage:

    # 1. Most flexible: explicit column mapping
    python -m scripts.seed_observations --csv path/to/data.csv \\
        --map rx_lat=latitude rx_lon=longitude observed_dbm=rsrp \\
              freq_hz=earfcn_freq_hz tx_lat=site_lat tx_lon=site_lon \\
              tx_power_dbm=eirp_dbm

    # 2. Auto-detect when columns already use canonical names
    python -m scripts.seed_observations --csv path/to/data.csv

    # 3. Dry-run to validate the mapping without writing
    python -m scripts.seed_observations --csv path/to/data.csv --dry-run

The script targets the database selected by ``DATABASE_URL`` (Railway
Postgres in production, SQLite locally) — no AWS credentials needed.
Once enough rows accumulate (≥ 1 000 by default) the next nightly
``retrain-coverage-model.yml`` will train a model that is genuinely no
longer 100 % synthetic.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from typing import Any, Dict, Iterator, List, Optional

# Allow `python scripts/seed_observations.py` (no -m) too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("seed_observations")

# Required canonical fields after mapping is applied. ``observation_store``
# fills sensible defaults for tx_height_m / rx_height_m / gains so they
# are not strictly required from the input CSV.
_REQUIRED = ("rx_lat", "rx_lon", "observed_dbm", "freq_hz", "tx_lat", "tx_lon")
_OPTIONAL = (
    "tower_id", "tx_height_m", "tx_power_dbm", "tx_gain_dbi",
    "rx_height_m", "rx_gain_dbi", "ts", "submitted_by",
)
_DEFAULTS: Dict[str, float] = {
    "tx_height_m": 35.0,
    "tx_power_dbm": 43.0,
    "tx_gain_dbi": 17.0,
    "rx_height_m": 1.5,
    "rx_gain_dbi": 0.0,
}


def _parse_freq(raw: str) -> float:
    """Best-effort conversion of a frequency string to Hz.

    Accepts: ``"1800000000"``, ``"1800 MHz"``, ``"700"``, ``"2.6 GHz"``.
    Empty / unparseable values raise ``ValueError`` so the caller can skip.
    """
    s = (raw or "").strip().lower()
    if not s:
        raise ValueError("empty frequency")
    mult = 1.0
    for suffix, m in (("ghz", 1e9), ("mhz", 1e6), ("khz", 1e3), ("hz", 1.0)):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            mult = m
            break
    val = float(s)
    if mult == 1.0 and val < 1e6:
        # Bare number that is too small to be Hz — assume MHz (common in
        # drive-test exports that record "earfcn_freq_mhz" without units).
        mult = 1e6
    return val * mult


def _coerce(row: Dict[str, str], mapping: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Apply column mapping + type coercion. Return None on validation failure."""
    out: Dict[str, Any] = {}
    for canonical, source in mapping.items():
        raw = row.get(source, "").strip() if source else ""
        if canonical == "freq_hz":
            try:
                out[canonical] = _parse_freq(raw)
            except ValueError:
                return None
        elif canonical in {"tower_id", "submitted_by"}:
            out[canonical] = raw or None
        else:
            try:
                out[canonical] = float(raw)
            except (TypeError, ValueError):
                if canonical in _REQUIRED:
                    return None
                # leave optional unset; observation_store fills defaults

    for k in _REQUIRED:
        if k not in out:
            return None

    # Sanity bounds: dBm in [-150, 0], lat/lon in valid ranges.
    if not (-150.0 <= out["observed_dbm"] <= 0.0):
        return None
    if not (-90.0 <= out["rx_lat"] <= 90.0 and -180.0 <= out["rx_lon"] <= 180.0):
        return None
    if not (-90.0 <= out["tx_lat"] <= 90.0 and -180.0 <= out["tx_lon"] <= 180.0):
        return None

    for k, v in _DEFAULTS.items():
        out.setdefault(k, v)
    out.setdefault("source", "csv_seed")
    return out


def _build_mapping(headers: List[str], overrides: List[str]) -> Dict[str, str]:
    """Combine auto-detection with explicit ``--map`` overrides."""
    headers_lc = {h.lower(): h for h in headers}
    aliases: Dict[str, List[str]] = {
        "rx_lat":       ["rx_lat", "latitude", "lat", "user_lat", "ue_lat"],
        "rx_lon":       ["rx_lon", "longitude", "lon", "lng", "user_lon", "ue_lon"],
        "observed_dbm": ["observed_dbm", "rsrp", "rssi", "signal_dbm", "rx_dbm",
                         "averagesignal", "signal"],
        "freq_hz":      ["freq_hz", "freq_mhz", "frequency", "earfcn_freq_hz",
                         "carrier_freq", "freq"],
        "tx_lat":       ["tx_lat", "site_lat", "tower_lat", "enb_lat", "bs_lat"],
        "tx_lon":       ["tx_lon", "site_lon", "tower_lon", "enb_lon", "bs_lon"],
        "tower_id":     ["tower_id", "site_id", "enb_id", "cell_id"],
        "tx_height_m":  ["tx_height_m", "antenna_height", "site_height"],
        "tx_power_dbm": ["tx_power_dbm", "eirp_dbm", "tx_power"],
        "tx_gain_dbi":  ["tx_gain_dbi", "antenna_gain"],
        "rx_height_m":  ["rx_height_m"],
        "rx_gain_dbi":  ["rx_gain_dbi"],
        "ts":           ["ts", "timestamp", "time"],
        "submitted_by": ["submitted_by", "operator", "source"],
    }
    mapping: Dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for cand in candidates:
            if cand.lower() in headers_lc:
                mapping[canonical] = headers_lc[cand.lower()]
                break

    for override in overrides:
        if "=" not in override:
            raise SystemExit(f"--map entry must be canonical=source, got: {override}")
        canonical, source = override.split("=", 1)
        canonical = canonical.strip()
        source = source.strip()
        if canonical not in _REQUIRED + _OPTIONAL:
            raise SystemExit(
                f"unknown canonical field {canonical!r}; "
                f"valid: {_REQUIRED + _OPTIONAL}"
            )
        if source not in headers:
            raise SystemExit(
                f"--map source column {source!r} not present in CSV "
                f"(headers: {headers})"
            )
        mapping[canonical] = source

    missing = [k for k in _REQUIRED if k not in mapping]
    if missing:
        raise SystemExit(
            f"could not map required fields {missing} from headers {headers}; "
            f"add --map entries explicitly"
        )
    return mapping


def _iter_csv(path: str) -> Iterator[Dict[str, str]]:
    """Stream a CSV file row by row, supporting plain or .gz inputs."""
    if path.endswith(".gz"):
        import gzip
        f = gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        f = open(path, "rt", encoding="utf-8", errors="replace")
    try:
        reader = csv.DictReader(f)
        for row in reader:
            yield row
    finally:
        f.close()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True,
                   help="Input CSV (plain or .gz) with measurement rows.")
    p.add_argument("--map", dest="map_", action="append", default=[],
                   metavar="canonical=source",
                   help="Override column mapping, e.g. observed_dbm=rsrp. Repeatable.")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N successfully coerced rows (0 = no limit).")
    p.add_argument("--batch-size", type=int, default=2000,
                   help="Insert batch size (default: 2000).")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate and report without writing to the database.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Peek at header row to build mapping before streaming.
    with (open(args.csv, "rt", encoding="utf-8", errors="replace")
          if not args.csv.endswith(".gz")
          else __import__("gzip").open(args.csv, "rt", encoding="utf-8",
                                       errors="replace")) as f:
        sample_reader = csv.DictReader(f)
        headers = sample_reader.fieldnames or []
    if not headers:
        logger.error("CSV has no header row: %s", args.csv)
        return 2
    mapping = _build_mapping(headers, args.map_)
    logger.info("column mapping: %s", mapping)

    from observation_store import ObservationStore
    store = ObservationStore() if not args.dry_run else None
    backend = store.backend if store else "dry-run"
    logger.info("target backend: %s", backend)

    batch: List[Dict[str, Any]] = []
    coerced = 0
    skipped = 0
    inserted = 0
    for row in _iter_csv(args.csv):
        rec = _coerce(row, mapping)
        if rec is None:
            skipped += 1
            continue
        coerced += 1
        batch.append(rec)
        if len(batch) >= args.batch_size:
            if store is not None:
                inserted += store.insert_observations_many(batch)
            batch.clear()
        if args.limit and coerced >= args.limit:
            break
    if batch and store is not None:
        inserted += store.insert_observations_many(batch)

    logger.info("done: coerced=%d skipped=%d inserted=%d (dry_run=%s)",
                coerced, skipped, inserted, args.dry_run)

    if store is not None:
        counts = store.counts()
        logger.info("link_observations now: %d", counts["link_observations"])
    return 0 if coerced > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
