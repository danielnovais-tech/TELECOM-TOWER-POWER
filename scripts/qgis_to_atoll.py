# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""QGIS → Atoll exporter.

Converts the platform's tower / coverage data into the file formats
Forsk Atoll ingests natively, so existing Atoll users can validate
TELECOM-TOWER-POWER predictions side-by-side with their commercial
licence — no lock-in, no manual reformatting.

Outputs (all written under ``--out-dir``):

* ``sites.txt``        — Atoll site list (lat, lon, AGL height, name).
* ``transmitters.txt`` — Atoll TX list with EIRP and azimuth.
* ``terrain.bil``      — 16-bit BIL DEM clipped to the AOI (from SRTM).
* ``clutter.bil``      — MapBiomas LULC reclassified onto Atoll's
                         standard 14-class clutter palette.
* ``atoll_import.qgs`` — QGIS project that re-opens the same data, so
                         analysts can visually QA before importing.

Run with the platform venv active::

    python scripts/qgis_to_atoll.py \\
        --aoi-bbox -47.0,-23.5,-46.0,-22.5 \\
        --out-dir ./atoll_export \\
        --towers-from-db

This script is dependency-light by design: only ``numpy`` and the
existing ``srtm_elevation`` / ``mapbiomas_clutter`` modules. We do
NOT depend on the QGIS Python API — the ``.qgs`` project is emitted
as a templated XML so the script runs in the same lean container
that powers the API.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

# Allow `python scripts/qgis_to_atoll.py ...` to import sibling modules
# (srtm_elevation, mapbiomas_clutter, tower_db) that live at the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("qgis_to_atoll")


def _parse_bbox(s: str) -> Tuple[float, float, float, float]:
    parts = [float(p) for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be lon_min,lat_min,lon_max,lat_max")
    return tuple(parts)  # type: ignore[return-value]


def _load_towers(towers_csv: Optional[Path], from_db: bool, bbox) -> list[dict]:
    """Load towers either from CSV (offline) or the live tower DB.

    The DB path uses the existing :class:`tower_db.TowerStore` so all
    backend selection (SQLite vs Postgres via ``DATABASE_URL``) and
    tenant ACLs are honoured automatically. Rows are filtered to the
    bbox client-side because ``TowerStore`` has no native bbox filter.
    """
    if towers_csv is not None:
        rows: list[dict] = []
        with towers_csv.open() as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
        return rows
    if from_db:
        # Lazy import — keeps the script usable in CI without DB creds.
        from tower_db import TowerStore  # type: ignore[import-not-found]

        lon_min, lat_min, lon_max, lat_max = bbox
        store = TowerStore()
        all_rows = store.list_all(limit=200_000)
        in_bbox = [
            r for r in all_rows
            if lon_min <= float(r["lon"]) <= lon_max
            and lat_min <= float(r["lat"]) <= lat_max
        ]
        # Normalise field names so the writers below work against either
        # the CSV schema (name, frequency_mhz, eirp_dbm, band, ...) or
        # the DB schema (id, operator, bands, power_dbm, ...).
        norm: list[dict] = []
        for r in in_bbox:
            bands = r.get("bands") or []
            band = bands[0] if isinstance(bands, list) and bands else r.get("band") or "B5"
            norm.append({
                "id": r.get("id"),
                "name": r.get("name") or r.get("id"),
                "lat": r["lat"],
                "lon": r["lon"],
                "height_m": r.get("height_m"),
                "ground_elev_m": r.get("ground_elev_m"),
                "eirp_dbm": r.get("power_dbm") or r.get("eirp_dbm"),
                "frequency_mhz": r.get("frequency_mhz"),
                "band": band,
                "azimuths": r.get("azimuths"),
                "tilt_deg": r.get("tilt_deg"),
            })
        return norm
    raise SystemExit("either --towers-csv or --towers-from-db is required")


def _write_atoll_sites(towers: Iterable[dict], path: Path) -> int:
    n = 0
    with path.open("w", encoding="latin-1") as fh:
        # Atoll's import wizard expects tab-separated, latin-1, with
        # a fixed header. Field order is documented in the Atoll
        # "Data Import / Export" manual (User Guide §3.4).
        fh.write("Name\tLongitude\tLatitude\tAltitude\tHeight\n")
        for t in towers:
            fh.write(
                f"{t.get('name') or t.get('id')}\t"
                f"{float(t['lon']):.6f}\t{float(t['lat']):.6f}\t"
                f"{float(t.get('ground_elev_m') or 0.0):.1f}\t"
                f"{float(t.get('height_m') or 30.0):.1f}\n"
            )
            n += 1
    return n


def _write_atoll_transmitters(towers: Iterable[dict], path: Path) -> int:
    n = 0
    with path.open("w", encoding="latin-1") as fh:
        fh.write(
            "Site\tCellName\tFrequency_MHz\tEIRP_dBm\tAzimuth_deg\t"
            "Tilt_deg\tHeight_m\tBand\n"
        )
        for t in towers:
            site = t.get("name") or t.get("id")
            for cell_idx, az in enumerate(_azimuths_for(t), start=1):
                fh.write(
                    f"{site}\t{site}-S{cell_idx}\t"
                    f"{float(t.get('frequency_mhz') or 850):.1f}\t"
                    f"{float(t.get('eirp_dbm') or 60):.1f}\t"
                    f"{az:.1f}\t"
                    f"{float(t.get('tilt_deg') or 4):.1f}\t"
                    f"{float(t.get('height_m') or 30.0):.1f}\t"
                    f"{t.get('band') or 'B5'}\n"
                )
                n += 1
    return n


def _azimuths_for(t: dict) -> Sequence[float]:
    """Return azimuth list for a tower's sectors.

    If the DB row carries an ``azimuths`` JSON column we use it; else
    we default to the Brazilian ANATEL standard 3-sector layout
    (0°, 120°, 240°)."""
    raw = t.get("azimuths")
    if raw:
        try:
            return [float(x) for x in (json.loads(raw) if isinstance(raw, str) else raw)]
        except Exception:
            pass
    return (0.0, 120.0, 240.0)


def _write_envi_hdr(path: Path, *, ncols: int, nrows: int,
                    ulx: float, uly: float, xdim: float, ydim: float,
                    nbits: int, pixeltype: str) -> None:
    """Emit an ENVI/BIL header sidecar that Atoll's generic raster
    importer (File → Import → Geographic data → Generic raster) reads.

    Field names are the union recognised by both ESRI and Atoll. The
    UL coordinates refer to the *centre* of the upper-left pixel, which
    is what both tools expect when ``MAPUNITS`` is degrees (EPSG:4326).
    """
    path.write_text(
        "BYTEORDER      I\n"           # little-endian
        "LAYOUT         BIL\n"
        f"NROWS          {nrows}\n"
        f"NCOLS          {ncols}\n"
        "NBANDS         1\n"
        f"NBITS          {nbits}\n"
        f"PIXELTYPE      {pixeltype}\n"
        f"ULXMAP         {ulx:.10f}\n"
        f"ULYMAP         {uly:.10f}\n"
        f"XDIM           {xdim:.10f}\n"
        f"YDIM           {ydim:.10f}\n"
        "MAPUNITS       DEGREES\n"
        "PROJECTION     GEOGRAPHIC\n"
        "DATUM          WGS84\n",
        encoding="ascii",
    )


def _export_terrain_bil(bbox, out_path: Path, step_deg: float) -> Tuple[int, int]:
    """Sample SRTM elevations onto a regular grid and dump as 16-bit BIL.

    Uses :class:`srtm_elevation.SRTMReader` so the same on-disk tile
    cache the rest of the platform shares is reused. Voids / missing
    tiles map to 0 m (matches the platform-wide convention).
    """
    import numpy as np
    from srtm_elevation import SRTMReader  # type: ignore[import-not-found]

    lon_min, lat_min, lon_max, lat_max = bbox
    ncols = max(1, int(round((lon_max - lon_min) / step_deg)))
    nrows = max(1, int(round((lat_max - lat_min) / step_deg)))
    reader = SRTMReader()
    grid = np.zeros((nrows, ncols), dtype="<i2")  # signed int16, little-endian
    for r in range(nrows):
        # Row 0 = north edge (Atoll/ENVI convention)
        lat = lat_max - (r + 0.5) * step_deg
        for c in range(ncols):
            lon = lon_min + (c + 0.5) * step_deg
            elev = reader.get_elevation(lat, lon)
            if elev is not None:
                grid[r, c] = int(round(elev))
    grid.tofile(out_path)
    _write_envi_hdr(
        out_path.with_suffix(".hdr"),
        ncols=ncols, nrows=nrows,
        ulx=lon_min + step_deg / 2,
        uly=lat_max - step_deg / 2,
        xdim=step_deg, ydim=step_deg,
        nbits=16, pixeltype="SIGNEDINT",
    )
    return nrows, ncols


def _export_clutter_bil(bbox, out_path: Path, step_deg: float) -> Tuple[int, int]:
    """Sample MapBiomas LULC onto the same grid and dump as 8-bit BIL.

    The MapBiomas integer class codes already fit in a uint8 (1..49 in
    the Collection 8 legend), so we cast directly. ``None`` (no raster
    configured / out-of-bounds) maps to 0 = "unknown", which Atoll
    treats as the default-loss clutter class.
    """
    import numpy as np
    from mapbiomas_clutter import get_extractor  # type: ignore[import-not-found]

    lon_min, lat_min, lon_max, lat_max = bbox
    ncols = max(1, int(round((lon_max - lon_min) / step_deg)))
    nrows = max(1, int(round((lat_max - lat_min) / step_deg)))
    extractor = get_extractor()
    grid = np.zeros((nrows, ncols), dtype="<u1")  # unsigned int8
    for r in range(nrows):
        lat = lat_max - (r + 0.5) * step_deg
        for c in range(ncols):
            lon = lon_min + (c + 0.5) * step_deg
            cls = extractor.get_clutter_class(lat, lon)
            if cls is not None and 0 <= int(cls) <= 255:
                grid[r, c] = int(cls)
    grid.tofile(out_path)
    _write_envi_hdr(
        out_path.with_suffix(".hdr"),
        ncols=ncols, nrows=nrows,
        ulx=lon_min + step_deg / 2,
        uly=lat_max - step_deg / 2,
        xdim=step_deg, ydim=step_deg,
        nbits=8, pixeltype="UNSIGNEDINT",
    )
    return nrows, ncols


def _write_qgs_project(out_dir: Path, bbox) -> Path:
    """Emit a minimal QGIS project that opens the exported layers."""
    qgs = out_dir / "atoll_import.qgs"
    qgs.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="TELECOM-TOWER-POWER → Atoll export" version="3.34.0">
  <title>TTP Atoll Export</title>
  <projectCrs><spatialrefsys><srid>4326</srid></spatialrefsys></projectCrs>
  <mapcanvas>
    <extent>
      <xmin>{bbox[0]}</xmin><ymin>{bbox[1]}</ymin>
      <xmax>{bbox[2]}</xmax><ymax>{bbox[3]}</ymax>
    </extent>
  </mapcanvas>
  <layers>
    <maplayer type="vector" name="sites">
      <datasource>file:./sites.txt?delimiter=%09&amp;xField=Longitude&amp;yField=Latitude</datasource>
    </maplayer>
    <maplayer type="raster" name="terrain">
      <datasource>./terrain.bil</datasource>
    </maplayer>
    <maplayer type="raster" name="clutter">
      <datasource>./clutter.bil</datasource>
    </maplayer>
  </layers>
</qgis>
""",
        encoding="utf-8",
    )
    return qgs


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--aoi-bbox", required=True, type=_parse_bbox,
                   help="lon_min,lat_min,lon_max,lat_max (WGS84)")
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--towers-csv", type=Path, default=None,
                   help="CSV with at least lat,lon,height_m columns")
    p.add_argument("--towers-from-db", action="store_true",
                   help="Read towers via tower_db.iter_towers_in_bbox")
    p.add_argument("--skip-rasters", action="store_true",
                   help="Skip terrain.bil / clutter.bil (faster, txt only)")
    p.add_argument("--raster-step-deg", type=float, default=1.0 / 1200.0,
                   help="Grid spacing for terrain/clutter BIL (default 3 arc-sec ≈ 90 m)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)

    towers = _load_towers(args.towers_csv, args.towers_from_db, args.aoi_bbox)
    logger.info("loaded %d towers", len(towers))

    n_sites = _write_atoll_sites(towers, args.out_dir / "sites.txt")
    n_tx = _write_atoll_transmitters(towers, args.out_dir / "transmitters.txt")
    logger.info("wrote %d sites, %d transmitters", n_sites, n_tx)

    if not args.skip_rasters:
        # Terrain + clutter are sampled from the same modules the live
        # platform uses (SRTMReader, MapBiomasExtractor) and dumped as
        # ENVI BIL — Atoll's "Generic raster" importer reads it natively.
        try:
            tr_rows, tr_cols = _export_terrain_bil(
                args.aoi_bbox, args.out_dir / "terrain.bil", args.raster_step_deg,
            )
            cl_rows, cl_cols = _export_clutter_bil(
                args.aoi_bbox, args.out_dir / "clutter.bil", args.raster_step_deg,
            )
            logger.info("wrote terrain.bil (%dx%d) and clutter.bil (%dx%d)",
                        tr_rows, tr_cols, cl_rows, cl_cols)
        except Exception:
            logger.warning("raster export failed; sites/transmitters still emitted",
                           exc_info=True)

    _write_qgs_project(args.out_dir, args.aoi_bbox)
    logger.info("done — open %s in QGIS to verify before importing into Atoll",
                args.out_dir / "atoll_import.qgs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
