# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Satellite-change robot.

Cross-references each tower's footprint against fresh Planet Labs
imagery (https://api.planet.com/data/v1) to flag sites where the
ground has likely changed since the last RF prediction was run —
new buildings, deforestation, vegetation regrowth, etc. The output
is a JSON report consumed by the GitHub Actions workflow, which
opens an issue when any site is flagged.

Why: Atoll/Infovista Planet RF predictions go stale silently as
clutter changes. Planet Labs' daily PSScene revisit means we can
detect those changes in near-real-time and trigger a re-prediction
*before* a customer notices the drift.

Inputs
------
* Sites: ``--sites-csv`` (lat,lon,name) or ``--sites-from-db``
  (uses TowerStore — same path as scripts/qgis_to_atoll.py).
* Search window: ``--since`` ISO date (default: 30 days ago).
* Buffer around each site: ``--buffer-km`` (default 2 km).
* Auth: ``$PLANET_API_KEY`` (Planet's standard HTTP-Basic key).

Output
------
JSON ``{"generated_at": ..., "since": ..., "sites": [{name, lat,
lon, scenes_found, clear_scenes, flagged, sample_scene_id}, ...]}``.
A site is *flagged* when ``clear_scenes >= --min-clear`` (default 1)
— i.e. there is at least one cloud-free image since the cutoff that
the operator can use to validate the area.

Offline / no-key behaviour
--------------------------
When ``PLANET_API_KEY`` is unset (e.g. CI without the secret), the
script still runs end-to-end but every site reports
``scenes_found=0, flagged=False, error="no-api-key"``. This keeps
the workflow green and lets the report artefact accumulate row
templates while the operator wires the secret.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as _dt
import json
import logging
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# Allow direct invocation: import sibling modules at repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("satellite_change_robot")

PLANET_QUICK_SEARCH_URL = "https://api.planet.com/data/v1/quick-search"
DEFAULT_ITEM_TYPES = ("PSScene",)


def _bbox_around(lat: float, lon: float, buffer_km: float) -> tuple[float, float, float, float]:
    """Return a (minx, miny, maxx, maxy) bbox in WGS84 around the point."""
    dlat = buffer_km / 111.0
    # cos(lat) shrinks longitude degree size as we move from equator.
    dlon = buffer_km / (111.0 * max(0.01, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _bbox_geojson(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    minx, miny, maxx, maxy = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
        ]],
    }


def _build_filter(bbox, since_iso: str, max_cloud: float) -> dict[str, Any]:
    """Compose Planet's nested AND filter: AOI + date range + cloud cover."""
    return {
        "type": "AndFilter",
        "config": [
            {
                "type": "GeometryFilter",
                "field_name": "geometry",
                "config": _bbox_geojson(bbox),
            },
            {
                "type": "DateRangeFilter",
                "field_name": "acquired",
                "config": {"gte": since_iso},
            },
            {
                "type": "RangeFilter",
                "field_name": "cloud_cover",
                "config": {"lte": float(max_cloud)},
            },
        ],
    }


def _planet_search(filt: dict[str, Any], item_types, api_key: str,
                   timeout: float = 30.0) -> dict[str, Any]:
    """POST a quick-search request and return the parsed response.

    Planet uses HTTP-Basic with the API key as username and an empty
    password (https://developers.planet.com/quickstart/apis/).
    """
    body = json.dumps({"item_types": list(item_types), "filter": filt}).encode("utf-8")
    auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        PLANET_QUICK_SEARCH_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
            "User-Agent": "ttp-satellite-change/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _scan_site(site: dict[str, Any], *, since_iso: str, buffer_km: float,
               max_cloud: float, clear_cloud: float, item_types,
               api_key: Optional[str]) -> dict[str, Any]:
    """Run one Planet quick-search per site and summarise results."""
    lat = float(site["lat"])
    lon = float(site["lon"])
    name = site.get("name") or site.get("id") or f"{lat:.4f},{lon:.4f}"
    out: dict[str, Any] = {
        "name": name, "lat": lat, "lon": lon,
        "scenes_found": 0, "clear_scenes": 0, "flagged": False,
        "sample_scene_id": None, "error": None,
    }
    if not api_key:
        out["error"] = "no-api-key"
        return out

    bbox = _bbox_around(lat, lon, buffer_km)
    filt = _build_filter(bbox, since_iso, max_cloud)
    try:
        data = _planet_search(filt, item_types, api_key)
    except urllib.error.HTTPError as exc:
        out["error"] = f"http-{exc.code}"
        return out
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        out["error"] = f"net-{type(exc).__name__}"
        return out
    except json.JSONDecodeError:
        out["error"] = "bad-json"
        return out

    features = data.get("features") or []
    out["scenes_found"] = len(features)
    clear = [
        f for f in features
        if (f.get("properties", {}).get("cloud_cover", 1.0) or 1.0) <= clear_cloud
    ]
    out["clear_scenes"] = len(clear)
    if clear:
        out["sample_scene_id"] = clear[0].get("id")
    return out


def _load_sites(sites_csv: Optional[Path], from_db: bool) -> list[dict[str, Any]]:
    if sites_csv is not None:
        with sites_csv.open() as fh:
            return list(csv.DictReader(fh))
    if from_db:
        from tower_db import TowerStore  # type: ignore[import-not-found]

        rows = TowerStore().list_all(limit=200_000)
        return [
            {"name": r.get("id"), "lat": r["lat"], "lon": r["lon"]}
            for r in rows
        ]
    raise SystemExit("either --sites-csv or --sites-from-db is required")


def _build_report(sites: list[dict[str, Any]], *, since_iso: str,
                  buffer_km: float, max_cloud: float, clear_cloud: float,
                  item_types, min_clear: int,
                  api_key: Optional[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for site in sites:
        row = _scan_site(
            site, since_iso=since_iso, buffer_km=buffer_km,
            max_cloud=max_cloud, clear_cloud=clear_cloud,
            item_types=item_types, api_key=api_key,
        )
        row["flagged"] = row["clear_scenes"] >= min_clear
        rows.append(row)
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "since": since_iso,
        "params": {
            "buffer_km": buffer_km,
            "max_cloud": max_cloud,
            "clear_cloud": clear_cloud,
            "min_clear": min_clear,
            "item_types": list(item_types),
        },
        "api_key_present": bool(api_key),
        "sites": rows,
        "flagged_count": sum(1 for r in rows if r["flagged"]),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--sites-csv", type=Path, default=None,
                   help="CSV with at least lat,lon (and optional name) columns")
    p.add_argument("--sites-from-db", action="store_true",
                   help="Pull sites from TowerStore (DATABASE_URL or sqlite)")
    p.add_argument("--since", default=None,
                   help="ISO date — only consider scenes acquired ≥ this. Default: 30 days ago.")
    p.add_argument("--buffer-km", type=float, default=2.0)
    p.add_argument("--max-cloud", type=float, default=0.5,
                   help="Max cloud_cover for the API filter (0..1)")
    p.add_argument("--clear-cloud", type=float, default=0.1,
                   help="Cloud_cover at/below which a scene counts as 'clear'")
    p.add_argument("--min-clear", type=int, default=1,
                   help="Flag a site when ≥ this many clear scenes were found")
    p.add_argument("--item-types", default=",".join(DEFAULT_ITEM_TYPES))
    p.add_argument("--output", required=True, type=Path,
                   help="Path to write the JSON report")
    p.add_argument("--fail-on-flagged", action="store_true",
                   help="Exit non-zero if any site was flagged (used by Actions)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.since:
        since_iso = args.since
    else:
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)
        since_iso = cutoff.isoformat(timespec="seconds")

    sites = _load_sites(args.sites_csv, args.sites_from_db)
    logger.info("scanning %d sites since %s", len(sites), since_iso)

    api_key = os.environ.get("PLANET_API_KEY") or None
    if not api_key:
        logger.warning("PLANET_API_KEY not set — emitting empty report (no-api-key)")

    item_types = tuple(t.strip() for t in args.item_types.split(",") if t.strip())
    report = _build_report(
        sites, since_iso=since_iso,
        buffer_km=args.buffer_km, max_cloud=args.max_cloud,
        clear_cloud=args.clear_cloud, item_types=item_types,
        min_clear=args.min_clear, api_key=api_key,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    logger.info("report written to %s — %d/%d sites flagged",
                args.output, report["flagged_count"], len(sites))

    if args.fail_on_flagged and report["flagged_count"] > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
