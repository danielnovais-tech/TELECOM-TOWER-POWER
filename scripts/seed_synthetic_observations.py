# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Generate synthetic *but physically-grounded* training labels for the
learned-propagation engine, drawn from real Brazilian tower locations
and the ITU-R P.1812 reference solver.

Why this exists
---------------
The OpenCelliD ``averageSignal`` field is 0 for 100 % of Brazilian rows
(verified 2026-04-30 over 54 549 records); no public crowdsourced RSSI
feed is currently usable for ground truth. Real drive-test campaigns
take weeks to ingest. This script unblocks the retrain pipeline by
manufacturing pseudo-observations whose label is the P.1812 prediction
plus log-normal shadowing (σ = 4 dB by default), so the trainer has
data shaped exactly like real measurements.

What this is NOT
----------------
A scientifically valid dataset. A model trained ONLY on these rows
will, at best, learn to imitate P.1812 + noise — never *beat* it. Its
sole purpose is to exercise the full
``trainer → TFLite → S3 → ECS hydration → registry`` path before real
data arrives. Every row inserted is tagged
``source = 'synthetic_p1812_v1'`` so downstream consumers can:

* count synthetic vs real volume in monitoring dashboards;
* exclude synthetic rows from training once real data dominates
  (``train_sionna.py --exclude-source synthetic_p1812_v1``);
* purge the seed in a single SQL statement when no longer wanted.

Usage::

    # Default: 200 towers × 10 receivers = 2 000 rows
    python -m scripts.seed_synthetic_observations

    # Smoke test — small batch, verbose
    python -m scripts.seed_synthetic_observations \\
        --n-towers 20 --receivers-per-tower 5 --verbose

    # Pre-flight without writing
    python -m scripts.seed_synthetic_observations --dry-run

Targets the database selected by ``DATABASE_URL`` (Railway in
production, SQLite locally) — same routing as the real importer.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from typing import Iterator, List, Optional

# Allow ``python scripts/seed_synthetic_observations.py`` (no -m) too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("seed_synthetic")

SOURCE_TAG = "synthetic_p1812_v1"

# ---------------------------------------------------------------------------
# Geometry helpers (kept self-contained — no extra deps).
# ---------------------------------------------------------------------------

_R_EARTH_KM = 6371.0


def _destination_point(lat: float, lon: float, bearing_rad: float, d_km: float):
    """Vincenty-light: spherical Earth, good to ~0.5 % at <100 km."""
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    dr = d_km / _R_EARTH_KM
    sin_lat2 = math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(bearing_rad)
    lat2 = math.asin(sin_lat2)
    y = math.sin(bearing_rad) * math.sin(dr) * math.cos(lat1)
    x = math.cos(dr) - math.sin(lat1) * sin_lat2
    lon2 = lon1 + math.atan2(y, x)
    return math.degrees(lat2), (math.degrees(lon2) + 540.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# Tower sampling
# ---------------------------------------------------------------------------

@dataclass
class Tower:
    tid: str
    lat: float
    lon: float
    height_m: float
    freq_hz: float
    eirp_dbm: float


def _sample_towers(n: int, rng: random.Random) -> List[Tower]:
    """Pick *n* random towers from ``tower_db``.

    Frequency is taken from ``freq_mhz`` (when set) otherwise picks a
    band reasonable for the operator/technology (defaults to 850 MHz —
    the most common SMP carrier in BR).
    Antenna height defaults to 35 m (Anatel "Class B" baseline).
    EIRP defaults to 60 dBm (43 dBm Pt + 17 dBi Gt).
    """
    from tower_db import TowerDB  # type: ignore[import-not-found]

    db = TowerDB()
    all_rows = list(db.list_all(limit=200_000))
    if not all_rows:
        raise SystemExit(
            "tower_db is empty — run scripts/load_brazil_towers.py or "
            "railway_load_towers.py first."
        )
    if n >= len(all_rows):
        sample = all_rows
    else:
        sample = rng.sample(all_rows, n)

    out: List[Tower] = []
    for r in sample:
        # Different stores expose the row dict slightly differently;
        # rely on the canonical columns documented in tower_db.py.
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
            tid = str(r.get("tower_id") or r.get("id") or f"{lat:.4f}_{lon:.4f}")
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        if abs(lat) < 0.01 and abs(lon) < 0.01:
            continue  # null-island sentinel
        freq_hz = 850e6
        try:
            f_mhz = float(r.get("freq_mhz") or 0)
            if f_mhz > 0:
                freq_hz = f_mhz * 1e6
        except (TypeError, ValueError):
            pass
        height_m = 35.0
        try:
            h = float(r.get("height_m") or 0)
            if 5.0 <= h <= 200.0:
                height_m = h
        except (TypeError, ValueError):
            pass
        out.append(Tower(tid=tid, lat=lat, lon=lon, height_m=height_m,
                         freq_hz=freq_hz, eirp_dbm=60.0))
    if not out:
        raise SystemExit("no usable towers after filtering")
    return out


# ---------------------------------------------------------------------------
# Profile + P.1812 evaluation
# ---------------------------------------------------------------------------

def _profile(tx_lat: float, tx_lon: float,
             rx_lat: float, rx_lon: float,
             n: int = 32) -> "tuple[list[float], list[float]]":
    """Linearly-spaced terrain profile, sampled from SRTM if available.

    Mirrors :func:`scripts.train_sionna._profile` so synthetic and real
    rows go through identical pre-processing.
    """
    # Total distance via haversine.
    lat1, lat2 = math.radians(tx_lat), math.radians(rx_lat)
    dlat = lat2 - lat1
    dlon = math.radians(rx_lon - tx_lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    d_total = 2 * _R_EARTH_KM * math.asin(min(1.0, math.sqrt(a)))
    d_km = [d_total * i / (n - 1) for i in range(n)]

    reader = None
    try:
        from srtm_elevation import SRTMReader  # type: ignore[import-not-found]
        reader = SRTMReader(data_dir=os.getenv("SRTM_DATA_DIR", "./srtm_data"))
    except Exception:
        pass

    h_m: List[float] = []
    for i in range(n):
        f = i / (n - 1)
        lat = tx_lat + (rx_lat - tx_lat) * f
        lon = tx_lon + (rx_lon - tx_lon) * f
        elev: Optional[float] = None
        if reader is not None:
            try:
                elev = reader.get_elevation(lat, lon)  # type: ignore[attr-defined]
            except Exception:
                elev = None
        h_m.append(float(elev) if elev is not None and elev > -1000 else 0.0)
    return d_km, h_m


def _p1812_lb(t: Tower, rx_lat: float, rx_lon: float,
              rx_height_m: float = 1.5) -> Optional[float]:
    from rf_engines import get_engine
    eng = get_engine("itu-p1812")
    if not eng.is_available():
        return None
    d_km, h_m = _profile(t.lat, t.lon, rx_lat, rx_lon)
    if d_km[-1] < 0.05:
        return None
    est = eng.predict_basic_loss(
        f_hz=t.freq_hz, d_km=d_km, h_m=h_m,
        htg=t.height_m, hrg=rx_height_m,
        phi_t=t.lat, lam_t=t.lon, phi_r=rx_lat, lam_r=rx_lon,
        pol=1, zone=4,
    )
    if est is None:
        return None
    return float(est.basic_loss_db)


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------

def _generate_rows(towers: List[Tower], per_tower: int,
                   shadowing_db: float, rng: random.Random) -> Iterator[dict]:
    """Yield observation dicts ready for ``insert_observations_many``."""
    for t in towers:
        for _ in range(per_tower):
            # Log-uniform distance in [0.5, 30] km — same range covered
            # by drive-test campaigns in suburban/rural BR. Log-uniform
            # is critical: linear sampling over-represents far links
            # where Lb is least informative.
            d_km = math.exp(rng.uniform(math.log(0.5), math.log(30.0)))
            bearing = rng.uniform(0.0, 2 * math.pi)
            rx_lat, rx_lon = _destination_point(t.lat, t.lon, bearing, d_km)
            lb = _p1812_lb(t, rx_lat, rx_lon)
            if lb is None:
                continue
            # Shadowing: log-normal in linear → gaussian in dB.
            lb_noisy = lb + rng.gauss(0.0, shadowing_db)
            prx = (t.eirp_dbm + 0.0) - lb_noisy  # rx gain = 0 dBi (UE)
            if prx > t.eirp_dbm or prx < -135.0:
                continue
            yield {
                "ts": time.time(),
                "tower_id": t.tid,
                "tx_lat": t.lat,
                "tx_lon": t.lon,
                "tx_height_m": t.height_m,
                "tx_power_dbm": t.eirp_dbm - 17.0,  # Pt = EIRP - Gt
                "tx_gain_dbi": 17.0,
                "rx_lat": rx_lat,
                "rx_lon": rx_lon,
                "rx_height_m": 1.5,
                "rx_gain_dbi": 0.0,
                "freq_hz": t.freq_hz,
                "observed_dbm": prx,
                "source": SOURCE_TAG,
                "submitted_by": "synthetic-seed",
            }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n-towers", type=int, default=200,
                   help="How many towers to sample (default: 200).")
    p.add_argument("--receivers-per-tower", type=int, default=10,
                   help="Receivers per tower (default: 10 → 2 000 rows total).")
    p.add_argument("--shadowing-db", type=float, default=4.0,
                   help="Log-normal shadowing σ in dB (default: 4.0 — typical "
                        "suburban value from ITU-R P.1546).")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    rng = random.Random(args.seed)

    logger.info("sampling %d towers from tower_db", args.n_towers)
    towers = _sample_towers(args.n_towers, rng)
    logger.info("got %d valid towers", len(towers))

    from observation_store import ObservationStore  # type: ignore[import-not-found]
    store = ObservationStore() if not args.dry_run else None
    if store is not None:
        logger.info("backend: %s", store.backend)

    batch: List[dict] = []
    generated = 0
    inserted = 0
    for row in _generate_rows(towers, args.receivers_per_tower,
                              args.shadowing_db, rng):
        generated += 1
        batch.append(row)
        if len(batch) >= args.batch_size:
            if store is not None:
                inserted += store.insert_observations_many(batch)
            batch.clear()
    if batch and store is not None:
        inserted += store.insert_observations_many(batch)

    logger.info("done: generated=%d inserted=%d (dry_run=%s)",
                generated, inserted, args.dry_run)
    if store is not None:
        counts = store.counts()
        logger.info("link_observations now: %d", counts["link_observations"])
    return 0 if generated > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
