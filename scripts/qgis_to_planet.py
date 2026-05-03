# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""TELECOM-TOWER-POWER → Infovista Planet exporter.

`Infovista Planet <https://www.infovista.com/products/planet>`_
(formerly Mentum Planet) is the Tier-1 desktop RF-planning tool used
by major Brazilian and global carriers. This exporter packages a
self-contained directory that a Planet operator can ingest via
"File → Import → Generic Project" and validate TTP predictions
side-by-side with their commercial licence.

Outputs (all under ``--out-dir``):

* ``sites.txt``           — Tab-separated, latin-1, Planet site list
                            (Name, Lat, Lon, Altitude, Height).
* ``transmitters.txt``    — Tab-separated, latin-1, Planet TX list.
* ``planet.par``          — Planet "Project parameters" header.
* ``terrain.bil`` (.hdr)  — 16-bit BIL DEM (m AMSL).
* ``clutter.bil`` (.hdr)  — 8-bit BIL clutter raster (Planet's default
                            14-class palette).
* ``clutter.cls``         — Planet clutter legend (class code → name +
                            default loss in dB).
* ``planet_import.qgs``   — QGIS project for visual QA before the
                            actual Planet import.

Usage (with the platform venv active)::

    python scripts/qgis_to_planet.py \\
        --aoi-bbox -47.0,-23.5,-46.0,-22.5 \\
        --out-dir ./planet_export \\
        --towers-from-db

Design notes
------------
File formats (BIL DEM/clutter, latin-1 site lists) deliberately
overlap with the Atoll exporter so we can reuse
``_export_terrain_bil`` and ``_export_clutter_bil`` without
modification — Planet's "Generic raster" loader and Atoll's are
near-identical descendants of ESRI's BIL spec. The differences are
in the header file (``planet.par``) and the clutter legend
(``clutter.cls``), both of which are emitted by this script.

This keeps the Atoll and Planet exporters tracking the same on-disk
DEM/clutter pixels — operators of either tool see the same model.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

# Make sibling modules importable when invoked as `python scripts/qgis_to_planet.py`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Atoll exporter's helpers — keeps the two paths in lockstep.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from qgis_to_atoll import (  # type: ignore[import-not-found]  # noqa: E402
    _azimuths_for,
    _export_clutter_bil,
    _export_terrain_bil,
    _load_towers,
    _parse_bbox,
)

logger = logging.getLogger("qgis_to_planet")


# ---------------------------------------------------------------------------
# Planet 14-class clutter palette
# ---------------------------------------------------------------------------
# Default mean attenuation (dB) per Planet 7.x default clutter
# template. Matches the legend Planet ships out of the box, so an
# operator who hasn't customised their clutter library can import
# our raster without remapping.
_PLANET_CLUTTER_CLASSES: Sequence[tuple[int, str, float]] = (
    ( 0, "Unknown",            0.0),
    ( 1, "Open",               0.0),
    ( 2, "Water",              0.0),
    ( 3, "Wetland",            1.0),
    ( 4, "Sparse_Vegetation",  2.0),
    ( 5, "Crops",              3.0),
    ( 6, "Pasture",            3.0),
    ( 7, "Shrub",              5.0),
    ( 8, "Forest_Open",        9.0),
    ( 9, "Forest_Dense",      14.0),
    (10, "Suburban_Low",       7.0),
    (11, "Suburban_Med",      11.0),
    (12, "Urban",             15.0),
    (13, "Urban_Dense",       20.0),
    (14, "Industrial",        12.0),
)


# ---------------------------------------------------------------------------
# Site / transmitter writers (latin-1, tab-separated — same as Atoll)
# ---------------------------------------------------------------------------
def _write_planet_sites(towers: Iterable[dict], path: Path) -> int:
    n = 0
    with path.open("w", encoding="latin-1") as fh:
        # Planet's import wizard accepts the same Atoll-shaped TSV.
        # Documented in Planet 7.x User Guide, "Site List Import".
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


def _write_planet_transmitters(towers: Iterable[dict], path: Path) -> int:
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


def _write_planet_par(out_dir: Path, bbox) -> Path:
    """Emit Planet's project header (``planet.par``).

    Planet expects an INI-style file describing the project's CRS,
    AOI bbox, and pointers to DEM/clutter rasters. The keys here are
    the minimum subset the import wizard requires.
    """
    par = out_dir / "planet.par"
    par.write_text(
        "[Project]\n"
        "Name=TELECOM-TOWER-POWER export\n"
        "CRS=EPSG:4326\n"
        f"BBoxLonMin={bbox[0]}\n"
        f"BBoxLatMin={bbox[1]}\n"
        f"BBoxLonMax={bbox[2]}\n"
        f"BBoxLatMax={bbox[3]}\n"
        "\n[Layers]\n"
        "Terrain=terrain.bil\n"
        "Clutter=clutter.bil\n"
        "Sites=sites.txt\n"
        "Transmitters=transmitters.txt\n"
        "ClutterClasses=clutter.cls\n",
        encoding="ascii",
    )
    return par


def _write_clutter_cls(path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Planet clutter classes (TTP-default 14-class palette)\n")
        fh.write("# code\tname\tmean_loss_dB\n")
        for code, name, loss in _PLANET_CLUTTER_CLASSES:
            fh.write(f"{code}\t{name}\t{loss:.1f}\n")


def _write_qgs_project(out_dir: Path, bbox) -> Path:
    qgs = out_dir / "planet_import.qgs"
    qgs.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="TELECOM-TOWER-POWER → Planet export" version="3.34.0">
  <title>TTP Planet Export</title>
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
    <maplayer type="raster" name="terrain"><datasource>./terrain.bil</datasource></maplayer>
    <maplayer type="raster" name="clutter"><datasource>./clutter.bil</datasource></maplayer>
  </layers>
</qgis>
""",
        encoding="utf-8",
    )
    return qgs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--aoi-bbox", required=True, type=_parse_bbox,
                   help="lon_min,lat_min,lon_max,lat_max (WGS84)")
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--towers-csv", type=Path, default=None,
                   help="CSV with at least lat,lon,height_m columns")
    p.add_argument("--towers-from-db", action="store_true",
                   help="Read towers via tower_db.TowerStore")
    p.add_argument("--skip-rasters", action="store_true",
                   help="Skip terrain.bil / clutter.bil (faster, txt-only)")
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

    n_sites = _write_planet_sites(towers, args.out_dir / "sites.txt")
    n_tx = _write_planet_transmitters(towers, args.out_dir / "transmitters.txt")
    _write_clutter_cls(args.out_dir / "clutter.cls")
    _write_planet_par(args.out_dir, args.aoi_bbox)
    logger.info("wrote %d sites and %d transmitter rows", n_sites, n_tx)

    if not args.skip_rasters:
        try:
            tr = _export_terrain_bil(
                args.aoi_bbox, args.out_dir / "terrain.bil", args.raster_step_deg,
            )
            cl = _export_clutter_bil(
                args.aoi_bbox, args.out_dir / "clutter.bil", args.raster_step_deg,
            )
            logger.info("wrote terrain.bil (%dx%d) and clutter.bil (%dx%d)",
                        tr[0], tr[1], cl[0], cl[1])
        except Exception:
            logger.warning("raster export failed; site/TX lists still emitted",
                           exc_info=True)

    _write_qgs_project(args.out_dir, args.aoi_bbox)
    logger.info("done — open %s in QGIS to verify before importing into Planet",
                args.out_dir / "planet_import.qgs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
