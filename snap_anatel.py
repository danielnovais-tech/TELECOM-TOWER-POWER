#!/usr/bin/env python3
"""
snap_anatel.py – Improve ANATEL geocoding precision by snapping each
ANATEL tower to the nearest OpenCelliD tower of the same operator within
a configurable radius (default: 5 km).

Why
---
ANATEL publishes ERB locations only as (city, state) pairs.  ``load_anatel.py``
turns these into (lat, lon) by looking up the IBGE municipality centroid
and adding ~800 m of random jitter so towers in the same city don't stack.
That is good enough for city-level analytics but adds up to ~5 km of
positional error per tower.

OpenCelliD provides crowdsourced GPS-tagged cells (median accuracy ~50 m).
For every operator that exists in both datasets, we can replace the noisy
ANATEL centroid+jitter coordinate with the closest OpenCelliD tower's
coordinate of the same operator, provided the candidate is within 5 km
(otherwise the OCID tower is too far away to be the same physical site).

Usage
-----
    # Snap in-place (writes back to the towers table)
    python snap_anatel.py

    # Dry-run: only report stats, don't modify the DB
    python snap_anatel.py --dry-run

    # Custom radius (km)
    python snap_anatel.py --max-km 3.0

CLI exit code: 0 on success.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tower_db import TowerStore, _haversine_km

# ── Tunables ──────────────────────────────────────────────────────
DEFAULT_MAX_KM = 5.0
# Spatial bucket size in degrees (~5.5 km at the equator → comfortably
# covers the default 5 km radius with at most a 3×3 neighbour scan).
_BUCKET_DEG = 0.05


def _bucket(lat: float, lon: float) -> Tuple[int, int]:
    return (int(lat / _BUCKET_DEG), int(lon / _BUCKET_DEG))


def build_index(
    ocid_towers: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[Tuple[int, int], List[Dict[str, Any]]]]:
    """Bucket OpenCelliD towers by (operator, lat-bucket, lon-bucket)."""
    index: Dict[str, Dict[Tuple[int, int], List[Dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for t in ocid_towers:
        op = t.get("operator")
        if not op:
            continue
        index[op][_bucket(t["lat"], t["lon"])].append(t)
    return index


def find_nearest(
    lat: float,
    lon: float,
    operator: str,
    index: Dict[str, Dict[Tuple[int, int], List[Dict[str, Any]]]],
    max_km: float,
) -> Optional[Tuple[Dict[str, Any], float]]:
    """Return (tower, km) of the closest same-operator OCID tower within
    *max_km*, or ``None`` if no candidate qualifies."""
    op_index = index.get(operator)
    if not op_index:
        return None
    bx, by = _bucket(lat, lon)
    best: Optional[Tuple[Dict[str, Any], float]] = None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for cand in op_index.get((bx + dx, by + dy), ()):
                d = _haversine_km(lat, lon, cand["lat"], cand["lon"])
                if d <= max_km and (best is None or d < best[1]):
                    best = (cand, d)
    return best


def snap_anatel(
    *,
    max_km: float = DEFAULT_MAX_KM,
    dry_run: bool = False,
    store: Optional[TowerStore] = None,
) -> Dict[str, Any]:
    """Snap every ``ANATEL_*`` tower to the closest same-operator
    ``OCID_*`` tower within *max_km*.

    Returns a dict with stats: ``{"anatel": N, "ocid": N, "snapped": N,
    "median_m": float, "p90_m": float, "max_m": float, "by_operator": {...}}``.
    """
    store = store or TowerStore()
    print(f"[snap] backend={store.backend} max_km={max_km} dry_run={dry_run}")

    t0 = time.time()
    rows = store.list_all(limit=10_000_000, owner=None)
    anatel = [r for r in rows if isinstance(r.get("id"), str) and r["id"].startswith("ANATEL_")]
    ocid = [r for r in rows if isinstance(r.get("id"), str) and r["id"].startswith("OCID_")]
    print(f"[snap] loaded {len(rows):,} towers in {time.time()-t0:.1f}s "
          f"(ANATEL={len(anatel):,}, OpenCelliD={len(ocid):,})")

    if not anatel or not ocid:
        print("[snap] nothing to do (need both ANATEL and OpenCelliD towers)")
        return {"anatel": len(anatel), "ocid": len(ocid), "snapped": 0,
                "median_m": 0.0, "p90_m": 0.0, "max_m": 0.0, "by_operator": {}}

    index = build_index(ocid)
    print(f"[snap] indexed {len(ocid):,} OCID towers across "
          f"{len(index)} operator(s) and "
          f"{sum(len(v) for v in index.values()):,} buckets")

    snapped: List[Dict[str, Any]] = []
    distances_km: List[float] = []
    by_operator: Dict[str, int] = defaultdict(int)
    no_match_by_op: Dict[str, int] = defaultdict(int)

    for t in anatel:
        match = find_nearest(t["lat"], t["lon"], t.get("operator", ""), index, max_km)
        if match is None:
            no_match_by_op[t.get("operator", "?")] += 1
            continue
        cand, dkm = match
        # Skip if already at exactly the OCID coordinate (idempotent).
        if abs(cand["lat"] - t["lat"]) < 1e-7 and abs(cand["lon"] - t["lon"]) < 1e-7:
            continue
        snapped.append({**t, "lat": cand["lat"], "lon": cand["lon"]})
        distances_km.append(dkm)
        by_operator[t.get("operator", "?")] += 1

    n = len(snapped)
    if not distances_km:
        median_m = p90_m = max_m = 0.0
    else:
        ds_m = sorted(d * 1000.0 for d in distances_km)
        median_m = statistics.median(ds_m)
        p90_m = ds_m[int(0.9 * (len(ds_m) - 1))]
        max_m = ds_m[-1]

    print(f"[snap] candidates: {n:,}/{len(anatel):,} "
          f"({100.0 * n / max(1, len(anatel)):.1f}%) within {max_km} km")
    print(f"[snap] distance: median={median_m:.0f} m  p90={p90_m:.0f} m  "
          f"max={max_m:.0f} m")
    print("[snap] by operator (snapped):")
    for op, c in sorted(by_operator.items(), key=lambda kv: -kv[1]):
        print(f"          {op:18s} {c:>7,}")
    if no_match_by_op:
        print("[snap] no OCID match within radius (top operators):")
        for op, c in sorted(no_match_by_op.items(), key=lambda kv: -kv[1])[:5]:
            print(f"          {op:18s} {c:>7,}")

    if dry_run:
        print("[snap] dry-run: not writing to DB")
    elif n == 0:
        print("[snap] nothing to write")
    else:
        print(f"[snap] writing {n:,} updates ...")
        # upsert_many preserves the row id (PK), so this is an in-place update.
        store.upsert_many(snapped)
        print("[snap] done.")

    return {
        "anatel": len(anatel),
        "ocid": len(ocid),
        "snapped": n,
        "median_m": median_m,
        "p90_m": p90_m,
        "max_m": max_m,
        "by_operator": dict(by_operator),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--max-km", type=float, default=DEFAULT_MAX_KM,
                        help=f"max snap radius in km (default {DEFAULT_MAX_KM})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report stats only, don't write to the DB")
    args = parser.parse_args(argv)

    stats = snap_anatel(max_km=args.max_km, dry_run=args.dry_run)
    return 0 if stats["snapped"] >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
