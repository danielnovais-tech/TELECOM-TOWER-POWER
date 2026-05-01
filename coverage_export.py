# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
coverage_export.py — Geo-format exporters for coverage grids.

Converts a list of ``coverage_predict.GridPoint`` (lat, lon, signal_dbm,
feasible) into formats consumed by external GIS / CAD tooling:

- ``geojson`` — RFC 7946 FeatureCollection (text/json).
- ``kml``     — Google Earth / QGIS (application/vnd.google-earth.kml+xml).
- ``shp``     — ESRI Shapefile zip bundle of (.shp, .shx, .dbf, .prj),
                consumable by QGIS, AutoCAD Map 3D, ArcGIS, etc.

Implementations are pure-Python (no GDAL/Fiona) so the Lambda
deployment package stays small. KML uses ``simplekml`` (≈40 kB),
Shapefile uses ``pyshp`` (≈25 kB). GeoJSON is hand-rolled.

Each cell is emitted as a Point feature. Signal strength is encoded
both as the ``signal_dbm`` attribute and as a coloured icon (KML)
or named bin (GeoJSON/SHP) so QGIS can apply a categorised renderer
out of the box.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import asdict
from typing import Any, Iterable, List, Sequence

logger = logging.getLogger(__name__)


# ─── Signal classification ──────────────────────────────────────────────

# Common cellular RSSI bins (dBm). Same buckets used by the heatmap UI.
_BINS: Sequence[tuple[float, str, str]] = (
    # (upper bound dBm, label, ABGR hex for KML)
    (-115.0, "no_signal", "ff0000ff"),   # red
    (-105.0, "very_weak", "ff0066ff"),   # orange-red
    (-95.0,  "weak",      "ff00aaff"),   # orange
    (-85.0,  "fair",      "ff00ffff"),   # yellow
    (-75.0,  "good",      "ff00ff00"),   # green
    (float("inf"), "excellent", "ff008800"),  # dark green
)


def classify(signal_dbm: float) -> tuple[str, str]:
    """Return (label, kml_color) for an RSSI value."""
    for upper, label, color in _BINS:
        if signal_dbm <= upper:
            return label, color
    return "excellent", "ff008800"


def _point_dict(p: Any) -> dict:
    """Normalise either a GridPoint dataclass or an already-dict point."""
    if isinstance(p, dict):
        return p
    try:
        return asdict(p)
    except TypeError:
        return {
            "lat": p.lat,
            "lon": p.lon,
            "signal_dbm": p.signal_dbm,
            "feasible": getattr(p, "feasible", False),
        }


# ─── GeoJSON ────────────────────────────────────────────────────────────

def to_geojson(points: Iterable[Any], *, meta: dict | None = None) -> str:
    """Serialise grid points as a GeoJSON FeatureCollection."""
    features: List[dict] = []
    for raw in points:
        p = _point_dict(raw)
        label, _ = classify(p["signal_dbm"])
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [p["lon"], p["lat"]],
            },
            "properties": {
                "signal_dbm": round(p["signal_dbm"], 2),
                "feasible": bool(p.get("feasible", False)),
                "class": label,
            },
        })
    fc = {
        "type": "FeatureCollection",
        "features": features,
    }
    if meta:
        # Top-level non-RFC fields are ignored by spec-strict parsers but
        # surface to consumers (QGIS, mapbox-gl) as layer metadata.
        fc["metadata"] = meta
    return json.dumps(fc, separators=(",", ":"))


# ─── KML ────────────────────────────────────────────────────────────────

def to_kml(points: Iterable[Any], *, name: str = "coverage", meta: dict | None = None) -> bytes:
    """Serialise grid points as a KML document (Google Earth / QGIS)."""
    try:
        import simplekml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "KML export requires the 'simplekml' package. Add "
            "'simplekml' to requirements.txt."
        ) from exc

    kml = simplekml.Kml(name=name)
    if meta:
        kml.document.description = json.dumps(meta, indent=2)

    # Pre-create a style per signal class so QGIS / Earth render bins
    # consistently.
    styles: dict[str, Any] = {}
    for _, label, color in _BINS:
        s = simplekml.Style()
        s.iconstyle.color = color
        s.iconstyle.scale = 0.6
        s.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
        s.labelstyle.scale = 0  # hide per-point label noise
        styles[label] = s

    for raw in points:
        p = _point_dict(raw)
        label, _ = classify(p["signal_dbm"])
        pnt = kml.newpoint(
            name=f"{round(p['signal_dbm'], 1)} dBm",
            coords=[(p["lon"], p["lat"])],
        )
        pnt.style = styles[label]
        pnt.extendeddata.newdata("signal_dbm", round(p["signal_dbm"], 2))
        pnt.extendeddata.newdata("class", label)
        pnt.extendeddata.newdata("feasible", str(bool(p.get("feasible", False))).lower())

    return kml.kml().encode("utf-8")


# ─── Shapefile (zipped bundle) ──────────────────────────────────────────

# WGS84 PRJ string — required by ArcGIS / AutoCAD; QGIS guesses without
# it but the export is unambiguous when it's present.
_WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)


def to_shapefile_zip(
    points: Iterable[Any],
    *,
    layer_name: str = "coverage",
) -> bytes:
    """Serialise grid points as a zipped ESRI Shapefile bundle."""
    try:
        import shapefile  # type: ignore[import-not-found]  # pyshp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Shapefile export requires the 'pyshp' package."
        ) from exc

    shp_buf = io.BytesIO()
    shx_buf = io.BytesIO()
    dbf_buf = io.BytesIO()

    w = shapefile.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf, shapeType=shapefile.POINT)
    # DBF schema (DBF field names are 8-byte ASCII; "feasible" is the
    # longest needed and fits.)
    w.field("signal_dbm", "N", size=10, decimal=2)
    w.field("class", "C", size=12)
    w.field("feasible", "L")

    for raw in points:
        p = _point_dict(raw)
        label, _ = classify(p["signal_dbm"])
        w.point(p["lon"], p["lat"])
        w.record(round(p["signal_dbm"], 2), label, bool(p.get("feasible", False)))

    w.close()

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{layer_name}.shp", shp_buf.getvalue())
        z.writestr(f"{layer_name}.shx", shx_buf.getvalue())
        z.writestr(f"{layer_name}.dbf", dbf_buf.getvalue())
        z.writestr(f"{layer_name}.prj", _WGS84_PRJ)
    return out.getvalue()


# ─── Dispatcher ─────────────────────────────────────────────────────────

# (content_type, file extension) per format, plus the encoder callable.
FORMATS = {
    "geojson": ("application/geo+json", "geojson"),
    "kml":     ("application/vnd.google-earth.kml+xml", "kml"),
    "shp":     ("application/zip", "zip"),
}


def export(
    points: Iterable[Any],
    fmt: str,
    *,
    name: str = "coverage",
    meta: dict | None = None,
) -> tuple[bytes, str, str]:
    """Encode ``points`` in ``fmt`` and return (payload, content_type, filename)."""
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r}")
    content_type, ext = FORMATS[fmt]
    if fmt == "geojson":
        payload = to_geojson(points, meta=meta).encode("utf-8")
    elif fmt == "kml":
        payload = to_kml(points, name=name, meta=meta)
    else:  # shp
        payload = to_shapefile_zip(points, layer_name=name)
    return payload, content_type, f"{name}.{ext}"
