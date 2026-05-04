# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Overpass building-footprint fetcher → GeoJSON.

Issues a single Overpass QL query for ``way["building"]`` polygons
inside the AOI bbox, resolves the node geometry, and writes a
GeoJSON ``FeatureCollection`` of polygons + a ``height_m`` property.

Height resolution rules (first match wins):

1. ``tags["height"]`` — parse leading float (strips ``"m"`` etc.).
2. ``tags["building:levels"]`` × ``LEVEL_HEIGHT_M`` (default 3.0 m).
3. ``DEFAULT_BUILDING_HEIGHT_M`` (8.0 m — single-storey commercial).

Rule 3 is intentionally conservative: an underestimated building
gives an over-optimistic mmWave coverage prediction (LOS where there
should be a shadow), which is the failure mode we want operators to
notice. An overestimate produces pessimistic coverage that is safe
to ship.

The fetcher is **read-only** and rate-respectful: it sets a
``User-Agent`` per the Overpass usage policy and refuses queries
larger than ``MAX_BBOX_DEG2`` (≈ 25 km² near the equator) — same
soft cap as ``build_mitsuba_scene.py``.

Network failures bubble up; this module never silently falls back to
a partial scene. The scene builder is responsible for deciding
whether to retry or abort.
"""
from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Public Overpass endpoint. The official rotation is fine for ad-hoc
# scene builds; for production we expect ops to mirror the data into
# a private Overpass instance and override via the env var.
DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# AOI safety cap — same value as BoundingBox in build_mitsuba_scene.
MAX_BBOX_DEG2 = 0.05

# Height fallback table (metres).
LEVEL_HEIGHT_M = 3.0
DEFAULT_BUILDING_HEIGHT_M = 8.0

# HTTP timeouts (seconds). Overpass returns < 5 s on small AOIs but we
# keep a generous ceiling for the 25 km² edge case.
_HTTP_TIMEOUT_S = 60

# UA per Overpass etiquette.
_USER_AGENT = (
    "telecom-tower-power-scene-builder/1.0 "
    "(+https://telecomtowerpower.com.br)"
)


def _parse_height(tags: Dict[str, str]) -> float:
    """Resolve a building height in metres from OSM tags."""
    raw = tags.get("height")
    if raw:
        try:
            stripped = raw.strip().split()[0].rstrip("m").rstrip()
            return float(stripped)
        except (ValueError, IndexError):
            logger.debug("could not parse height='%s'", raw)
    raw_levels = tags.get("building:levels")
    if raw_levels:
        try:
            return float(raw_levels) * LEVEL_HEIGHT_M
        except ValueError:
            logger.debug("could not parse building:levels='%s'", raw_levels)
    return DEFAULT_BUILDING_HEIGHT_M


def _build_query(bbox: Tuple[float, float, float, float]) -> str:
    """Compose the Overpass QL query for ``way["building"]`` in bbox."""
    south, west, north, east = bbox
    # ``out body geom`` returns each way with inline geometry — saves a
    # second roundtrip for node resolution.
    return (
        "[out:json][timeout:50];"
        f'(way["building"]({south},{west},{north},{east}););'
        "out body geom;"
    )


def fetch_buildings(
    bbox: Tuple[float, float, float, float],
    *,
    overpass_url: str = DEFAULT_OVERPASS_URL,
    timeout_s: int = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Fetch building footprints inside ``bbox`` and return a GeoJSON dict.

    The returned dict is a ``FeatureCollection`` of ``Polygon`` features
    with ``properties = {osm_id, building, height_m, height_source}``.

    Raises ``RuntimeError`` for non-200 responses or JSON parse errors.
    """
    south, west, north, east = bbox
    area = (north - south) * (east - west)
    if area > MAX_BBOX_DEG2:
        raise ValueError(
            f"AOI {area:.4f} deg² exceeds Overpass soft limit "
            f"{MAX_BBOX_DEG2:.4f} deg² (~25 km²). Tile the build first."
        )

    query = _build_query(bbox)
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    req = urllib.request.Request(
        overpass_url,
        data=body,
        headers={"User-Agent": _USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    logger.info("Overpass query: bbox=(%s,%s,%s,%s) url=%s",
                south, west, north, east, overpass_url)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as ex:
        raise RuntimeError(
            f"Overpass HTTP {ex.code}: {ex.reason}"
        ) from ex
    except urllib.error.URLError as ex:
        raise RuntimeError(f"Overpass network error: {ex}") from ex

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as ex:
        raise RuntimeError(f"Overpass returned non-JSON body: {ex}") from ex

    return _to_geojson(data.get("elements", []))


def _to_geojson(elements: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert Overpass ``elements`` to a GeoJSON FeatureCollection."""
    features: List[Dict[str, Any]] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 4:
            # Need at least 3 distinct points + closure.
            continue
        # OSM ways for buildings should be closed; some authors leave
        # the last node off. We close defensively.
        ring = [(node["lon"], node["lat"]) for node in geom]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) < 4:
            continue
        tags = el.get("tags") or {}
        height = _parse_height(tags)
        if "height" in tags:
            source = "tag:height"
        elif "building:levels" in tags:
            source = "tag:building_levels"
        else:
            source = "default"
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "osm_id": el.get("id"),
                "building": tags.get("building", "yes"),
                "height_m": height,
                "height_source": source,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _polygon_area_m2(ring: List[Tuple[float, float]]) -> float:
    """Approximate planar area in m² for a small WGS84 polygon.

    Uses an equirectangular projection centred on the ring centroid.
    Accurate to ~0.5% inside a 5 km AOI — enough for sanity-check
    summaries; not meant for any downstream physics.
    """
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    cos_lat = math.cos(math.radians(lat0))
    pts = [(math.radians(lon) * 6371000.0 * cos_lat,
            math.radians(lat) * 6371000.0) for lon, lat in ring]
    s = 0.0
    for i in range(len(pts) - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return abs(s) / 2.0


def summarise(geojson: Dict[str, Any]) -> Dict[str, Any]:
    """Return a small dict for the manifest: count + height stats."""
    feats = geojson.get("features") or []
    if not feats:
        return {"count": 0}
    heights = [f["properties"]["height_m"] for f in feats]
    sources: Dict[str, int] = {}
    for f in feats:
        sources[f["properties"]["height_source"]] = (
            sources.get(f["properties"]["height_source"], 0) + 1
        )
    areas = [
        _polygon_area_m2(f["geometry"]["coordinates"][0]) for f in feats
    ]
    return {
        "count": len(feats),
        "height_min_m": round(min(heights), 2),
        "height_max_m": round(max(heights), 2),
        "height_mean_m": round(sum(heights) / len(heights), 2),
        "height_sources": sources,
        "footprint_area_total_m2": round(sum(areas), 1),
    }
