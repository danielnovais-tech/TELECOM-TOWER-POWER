#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Populate ``planet_ndvi_cache.json`` from Planet Data API quick-search.

The Sionna feature schema v2 consumes a per-cell NDVI delta in
``[-1, +1]``. Computing a true NDVI from PSScene rasters requires
downloading 4-band imagery (cost + GBs of egress), which is out of
scope for periodic CI runs. This script ships a *coverage-cadence
proxy*: for each grid cell we ask Planet how many cloud-free PSScene
acquisitions occurred in the last 30 days vs. the prior 30 days, and
encode the relative change as a delta in ``[-1, +1]``. A cell with
many recent clear scenes (relative to its baseline) is more likely
to be in an active growing season; one with fewer is either cloudy
or dormant. The model treats this as a noisy signal and learns to
weight it accordingly \u2014 the missing flag still fires for any cell
the proxy can't measure.

Inputs
------
- ``--towers``: JSON file ``[{"id": "...", "lat": ..., "lon": ...}, ...]``
  driving the AOI list. Default ``towers_for_ndvi.json``.
- ``--out``: cache file path (default ``planet_ndvi_cache.json``).
- ``--resolution-deg``: grid resolution; lat/lon are quantised before
  the API call so multiple towers in the same cell share one query.
- ``--planet-api-key``: defaults to ``PLANET_API_KEY`` env var. Empty
  password HTTP-Basic per Planet docs.
- ``--dry-run``: print the planned cell list but make no API calls.

Output cache schema is the one consumed by ``planet_ndvi.py``.

Failure mode
------------
Any cell whose API call fails or returns no scenes is omitted from
the cache, NOT written as zero. The runtime extractor's missing flag
catches that, so a partial run still produces a correct cache.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("refresh_ndvi_cache")

_API_URL = "https://api.planet.com/data/v1/quick-search"
_ITEM_TYPE = "PSScene"


def _quantise(value: float, resolution_deg: float) -> float:
    if resolution_deg <= 0:
        return value
    return round(value / resolution_deg) * resolution_deg


def _cells_from_towers(
    towers: Iterable[dict], resolution_deg: float
) -> List[Tuple[float, float]]:
    seen: set = set()
    out: List[Tuple[float, float]] = []
    for t in towers:
        try:
            lat = float(t["lat"])
            lon = float(t["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        qlat = _quantise(lat, resolution_deg)
        qlon = _quantise(lon, resolution_deg)
        key = (round(qlat, 4), round(qlon, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append((qlat, qlon))
    return out


def _bbox_geojson(lat: float, lon: float, half_deg: float) -> dict:
    """A small square AOI centred on the cell. We use the same half-side
    as the cell resolution so the AOI exactly covers the cell."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - half_deg, lat - half_deg],
            [lon + half_deg, lat - half_deg],
            [lon + half_deg, lat + half_deg],
            [lon - half_deg, lat + half_deg],
            [lon - half_deg, lat - half_deg],
        ]],
    }


def _scene_count(
    api_key: str,
    aoi: dict,
    start: _dt.datetime,
    end: _dt.datetime,
    *,
    max_cloud: float = 0.2,
    timeout: float = 30.0,
) -> Optional[int]:
    """Return number of PSScene assets in the window, or None on error."""
    body = {
        "item_types": [_ITEM_TYPE],
        "filter": {
            "type": "AndFilter",
            "config": [
                {"type": "GeometryFilter", "field_name": "geometry", "config": aoi},
                {"type": "DateRangeFilter", "field_name": "acquired", "config": {
                    "gte": start.isoformat().replace("+00:00", "Z"),
                    "lte": end.isoformat().replace("+00:00", "Z"),
                }},
                {"type": "RangeFilter", "field_name": "cloud_cover", "config": {
                    "lte": max_cloud,
                }},
            ],
        },
    }
    auth = base64.b64encode(f"{api_key}:".encode()).decode()
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        logger.warning("planet quick-search failed: %s", e)
        return None
    feats = payload.get("features", [])
    if not isinstance(feats, list):
        return None
    return len(feats)


def _delta_from_counts(recent: int, prior: int) -> float:
    """Encode (recent - prior) / max(1, recent + prior) into [-1, +1].

    Symmetric around zero, bounded, and degenerate-input safe.
    """
    denom = max(1, recent + prior)
    raw = (recent - prior) / denom
    if raw > 1.0:
        return 1.0
    if raw < -1.0:
        return -1.0
    return float(raw)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--towers", default="towers_for_ndvi.json",
                   help="JSON list of {id, lat, lon} entries")
    p.add_argument("--out", default="planet_ndvi_cache.json",
                   help="Output cache path consumed by planet_ndvi.py")
    p.add_argument("--resolution-deg", type=float, default=0.05,
                   help="Grid resolution; cells share an API call")
    p.add_argument("--window-days", type=int, default=30,
                   help="Length of the 'recent' and 'prior' windows")
    p.add_argument("--max-cloud", type=float, default=0.2,
                   help="Max cloud_cover ratio per scene")
    p.add_argument("--planet-api-key", default=os.getenv("PLANET_API_KEY"),
                   help="Planet API key (defaults to $PLANET_API_KEY)")
    p.add_argument("--rate-sleep-s", type=float, default=0.25,
                   help="Sleep between cells to respect Planet rate limits")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the cell list without making any API call")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    towers_path = Path(args.towers)
    if not towers_path.is_file():
        logger.error("towers file %s not found", towers_path)
        return 1
    try:
        towers = json.loads(towers_path.read_text())
    except (OSError, ValueError):
        logger.exception("cannot parse %s", towers_path)
        return 1
    if not isinstance(towers, list):
        logger.error("towers file must be a JSON list")
        return 1

    cells = _cells_from_towers(towers, args.resolution_deg)
    logger.info("collapsed %d towers \u2192 %d unique cells (res=%.3f\u00b0)",
                len(towers), len(cells), args.resolution_deg)

    if args.dry_run:
        for lat, lon in cells:
            print(f"{lat:.2f},{lon:.2f}")
        return 0

    if not args.planet_api_key:
        logger.error("PLANET_API_KEY missing; pass --planet-api-key or set env")
        return 2

    now = _dt.datetime.now(_dt.timezone.utc)
    recent_start = now - _dt.timedelta(days=args.window_days)
    prior_start = now - _dt.timedelta(days=2 * args.window_days)

    out_cells: Dict[str, float] = {}
    half = args.resolution_deg / 2.0
    for i, (lat, lon) in enumerate(cells, 1):
        aoi = _bbox_geojson(lat, lon, half)
        recent = _scene_count(args.planet_api_key, aoi, recent_start, now,
                              max_cloud=args.max_cloud)
        prior = _scene_count(args.planet_api_key, aoi, prior_start, recent_start,
                             max_cloud=args.max_cloud)
        if recent is None or prior is None:
            logger.warning("skipping cell %.2f,%.2f \u2014 API failure", lat, lon)
            continue
        delta = _delta_from_counts(recent, prior)
        out_cells[f"{lat:.2f},{lon:.2f}"] = delta
        if i % 25 == 0:
            logger.info("progress: %d/%d cells", i, len(cells))
        time.sleep(max(0.0, args.rate_sleep_s))

    payload = {
        "schema": "ndvi-delta-v1",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "resolution_deg": args.resolution_deg,
        "cells": out_cells,
    }
    Path(args.out).write_text(json.dumps(payload, separators=(",", ":")))
    logger.info("wrote %d cells to %s", len(out_cells), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
