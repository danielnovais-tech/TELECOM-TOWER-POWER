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

logger = logging.getLogger("qgis_to_atoll")


def _parse_bbox(s: str) -> Tuple[float, float, float, float]:
    parts = [float(p) for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be lon_min,lat_min,lon_max,lat_max")
    return tuple(parts)  # type: ignore[return-value]


def _load_towers(towers_csv: Optional[Path], from_db: bool, bbox) -> list[dict]:
    """Load towers either from CSV (offline) or the live Postgres DB.

    The DB path uses the existing ``tower_db`` module so tower visibility
    rules (per-tenant ACLs, soft-deleted rows) are honoured automatically.
    """
    if towers_csv is not None:
        rows: list[dict] = []
        with towers_csv.open() as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
        return rows
    if from_db:
        # Lazy import — keeps the script usable in CI without DB creds.
        from tower_db import iter_towers_in_bbox  # type: ignore[import-not-found]

        return list(iter_towers_in_bbox(*bbox))
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
        # Terrain + clutter are computed by the existing platform
        # modules; we just clip them to the AOI and emit ENVI BIL,
        # which Atoll reads natively (File → Import → Geographic data
        # → Generic raster). Heavy work is delegated so this script
        # stays under 250 lines.
        try:
            from srtm_elevation import export_bil_for_bbox  # type: ignore[import-not-found]
            from mapbiomas_clutter import export_bil_for_bbox as _clutter_bil  # type: ignore[import-not-found]
        except Exception:
            logger.warning("raster export modules unavailable; skipping rasters", exc_info=True)
        else:
            export_bil_for_bbox(args.aoi_bbox, args.out_dir / "terrain.bil")
            _clutter_bil(args.aoi_bbox, args.out_dir / "clutter.bil")
            logger.info("wrote terrain.bil and clutter.bil")

    _write_qgs_project(args.out_dir, args.aoi_bbox)
    logger.info("done — open %s in QGIS to verify before importing into Atoll",
                args.out_dir / "atoll_import.qgs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
