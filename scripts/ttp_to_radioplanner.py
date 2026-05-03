# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""TELECOM-TOWER-POWER → RadioPlanner 3.0 exporter.

`RadioPlanner 3.0 <https://www.radioplanner.com/>`_ is a Windows desktop
RF-planning tool with a free *trial* tier and a paid commercial tier
that's popular with Brazilian WISPs and small carriers because it
runs offline and ingests CSV + ESRI ASCII grids natively (no
proprietary binary loaders required).

This script lets a TTP-only customer hand a RadioPlanner operator a
self-contained directory they can drop straight into "File → Import →
Project from folder" and see the same towers / DEM / clutter the
platform uses, side-by-side with their commercial licence's results.

Outputs (all under ``--out-dir``):

* ``sites.csv``           — Name, Lat, Lon, GroundElev_m, Height_m.
* ``transmitters.csv``    — Site, Sector, Freq_MHz, EIRP_dBm, Az, Tilt,
                            Height, Band, Antenna.
* ``terrain.asc``         — ESRI ASCII Grid DEM (16-bit ints, m AMSL).
* ``clutter.asc``         — ESRI ASCII Grid clutter (RadioPlanner
                            14-class palette derived from MapBiomas).
* ``clutter_legend.txt``  — Class code → name + default loss in dB.
* ``radioplanner_import.qgs`` — QGIS project for visual QA before
                                 the actual RadioPlanner import.

Usage (with the platform venv active)::

    python scripts/ttp_to_radioplanner.py \\
        --aoi-bbox -47.0,-23.5,-46.0,-22.5 \\
        --out-dir ./radioplanner_export \\
        --towers-from-db

Design notes
------------
We deliberately reuse the helpers already battle-tested in
``scripts/qgis_to_atoll.py`` (``_load_towers``, ``_azimuths_for``,
``_parse_bbox``) so the two exporters always see the same tower
roster — RadioPlanner customers and Atoll customers should get
identical site lists. The raster writer is *different* (ASCII grid
vs ENVI BIL) because RadioPlanner has flaky BIL support and a
rock-solid ASCII Grid reader.

This script is dependency-light: only ``numpy``, ``srtm_elevation``,
``mapbiomas_clutter``, and the Atoll script (for the shared helpers).
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

# Make sibling modules importable when invoked as `python scripts/ttp_to_radioplanner.py`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Reuse the Atoll exporter's helpers — keeps the two paths in lockstep.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from qgis_to_atoll import (  # type: ignore[import-not-found]  # noqa: E402
    _azimuths_for,
    _load_towers,
    _parse_bbox,
)

logger = logging.getLogger("ttp_to_radioplanner")


# ---------------------------------------------------------------------------
# RadioPlanner 14-class clutter palette
# ---------------------------------------------------------------------------
# Default mean attenuation (dB) per RadioPlanner 3.0 user guide §5.2.
# The MapBiomas Collection 8 legend has ~30 classes; we collapse them
# into RadioPlanner's 14-class taxonomy so the operator can use the
# vendor-provided default-loss lookup table without hand-tuning.
_RP_CLUTTER_CLASSES: Sequence[Tuple[int, str, float]] = (
    ( 1, "Open",                0.0),
    ( 2, "Water",               0.0),
    ( 3, "Wetland",             1.0),
    ( 4, "Sparse_Veg",          2.0),
    ( 5, "Crops",               3.0),
    ( 6, "Pasture",             3.0),
    ( 7, "Shrub",               5.0),
    ( 8, "Forest_Open",         9.0),
    ( 9, "Forest_Dense",       14.0),
    (10, "Suburban_Low",        7.0),
    (11, "Suburban_Med",       11.0),
    (12, "Urban",              15.0),
    (13, "Urban_Dense",        20.0),
    (14, "Industrial",         12.0),
)

# MapBiomas-Collection-8 → RadioPlanner-14 reclass table.
# Source: docs/clutter-mapping.md (TTP standard mapping). Codes not
# listed fall through to class 1 ("Open") which is RadioPlanner's
# safe default (zero added loss).
_MAPBIOMAS_TO_RP: dict[int, int] = {
    # Forest formations / plantations (dense canopy)
    1: 9, 3: 9, 4: 8, 5: 8, 6: 8, 49: 8,
    # Savanna / shrub
    12: 7, 32: 7, 50: 7,
    # Wetlands / mangroves
    10: 3, 11: 3,
    # Pasture / grassland
    13: 6, 15: 6, 21: 6,
    # Crops
    14: 5, 18: 5, 19: 5, 20: 5, 36: 5, 39: 5, 40: 5, 41: 5, 46: 5, 47: 5, 48: 5,
    # Urban infrastructure
    24: 12, 25: 13, 30: 14,
    # Mining / bare ground
    22: 1, 23: 1, 29: 1,
    # Water / coast
    26: 2, 31: 2, 33: 2, 34: 2, 27: 1,
}


def _reclass_to_rp(mb_class: int) -> int:
    """Map a MapBiomas Collection 8 code onto the RadioPlanner palette."""
    return _MAPBIOMAS_TO_RP.get(int(mb_class), 1)


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------
def _write_sites_csv(towers: Iterable[dict], path: Path) -> int:
    """Write RadioPlanner sites.csv (UTF-8, comma-separated, RP §3.1)."""
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Name", "Latitude", "Longitude", "GroundElev_m", "Height_m"])
        for t in towers:
            w.writerow([
                t.get("name") or t.get("id"),
                f"{float(t['lat']):.6f}",
                f"{float(t['lon']):.6f}",
                f"{float(t.get('ground_elev_m') or 0.0):.1f}",
                f"{float(t.get('height_m') or 30.0):.1f}",
            ])
            n += 1
    return n


def _write_transmitters_csv(towers: Iterable[dict], path: Path) -> int:
    """Write RadioPlanner transmitters.csv (RP §3.2)."""
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "Site", "Sector", "Frequency_MHz", "EIRP_dBm",
            "Azimuth_deg", "Tilt_deg", "Height_m", "Band", "Antenna",
        ])
        for t in towers:
            site = t.get("name") or t.get("id")
            for cell_idx, az in enumerate(_azimuths_for(t), start=1):
                w.writerow([
                    site,
                    f"{site}-S{cell_idx}",
                    f"{float(t.get('frequency_mhz') or 850):.1f}",
                    f"{float(t.get('eirp_dbm') or 60):.1f}",
                    f"{az:.1f}",
                    f"{float(t.get('tilt_deg') or 4):.1f}",
                    f"{float(t.get('height_m') or 30.0):.1f}",
                    t.get("band") or "B5",
                    "Omni",   # RadioPlanner ships a generic "Omni" pattern
                ])
                n += 1
    return n


# ---------------------------------------------------------------------------
# ESRI ASCII Grid raster writers
# ---------------------------------------------------------------------------
def _write_ascii_grid(path: Path, grid, *, lon_min: float, lat_min: float,
                      step_deg: float, nodata: int) -> None:
    """Emit an ESRI ASCII Grid (.asc).

    RadioPlanner 3.0 reads this format natively (File → Import →
    Generic Grid). The header convention places ``xllcorner`` /
    ``yllcorner`` at the *lower-left corner* of the grid, with the
    cell size assumed square (which it is for our equal-step grids).
    """
    nrows, ncols = grid.shape
    header = (
        f"ncols        {ncols}\n"
        f"nrows        {nrows}\n"
        f"xllcorner    {lon_min:.10f}\n"
        f"yllcorner    {lat_min:.10f}\n"
        f"cellsize     {step_deg:.10f}\n"
        f"NODATA_value {nodata}\n"
    )
    # ASCII Grid orders rows top-to-bottom (row 0 = north). We already
    # build the grid in that orientation in the samplers below.
    with path.open("w", encoding="ascii") as fh:
        fh.write(header)
        for r in range(nrows):
            fh.write(" ".join(str(int(v)) for v in grid[r]))
            fh.write("\n")


def _export_terrain_asc(bbox, out_path: Path, step_deg: float) -> Tuple[int, int]:
    """Sample SRTM elevations and dump as ESRI ASCII Grid (m AMSL)."""
    import numpy as np
    from srtm_elevation import SRTMReader  # type: ignore[import-not-found]

    lon_min, lat_min, lon_max, lat_max = bbox
    ncols = max(1, int(round((lon_max - lon_min) / step_deg)))
    nrows = max(1, int(round((lat_max - lat_min) / step_deg)))
    reader = SRTMReader()
    grid = np.full((nrows, ncols), -9999, dtype="i4")
    for r in range(nrows):
        # Row 0 = north edge.
        lat = lat_max - (r + 0.5) * step_deg
        for c in range(ncols):
            lon = lon_min + (c + 0.5) * step_deg
            elev = reader.get_elevation(lat, lon)
            if elev is not None:
                grid[r, c] = int(round(elev))
    _write_ascii_grid(
        out_path, grid,
        lon_min=lon_min, lat_min=lat_min, step_deg=step_deg, nodata=-9999,
    )
    return nrows, ncols


def _export_clutter_asc(bbox, out_path: Path, step_deg: float) -> Tuple[int, int]:
    """Sample MapBiomas LULC, reclass to RadioPlanner 14-class palette."""
    import numpy as np
    from mapbiomas_clutter import get_extractor  # type: ignore[import-not-found]

    lon_min, lat_min, lon_max, lat_max = bbox
    ncols = max(1, int(round((lon_max - lon_min) / step_deg)))
    nrows = max(1, int(round((lat_max - lat_min) / step_deg)))
    extractor = get_extractor()
    grid = np.zeros((nrows, ncols), dtype="i2")  # default class 1 ("Open") via reclass
    for r in range(nrows):
        lat = lat_max - (r + 0.5) * step_deg
        for c in range(ncols):
            lon = lon_min + (c + 0.5) * step_deg
            cls = extractor.get_clutter_class(lat, lon)
            grid[r, c] = _reclass_to_rp(cls if cls is not None else 0)
    _write_ascii_grid(
        out_path, grid,
        lon_min=lon_min, lat_min=lat_min, step_deg=step_deg, nodata=0,
    )
    return nrows, ncols


def _write_clutter_legend(path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# RadioPlanner 3.0 14-class clutter palette\n")
        fh.write("# code\tname\tmean_loss_dB\n")
        for code, name, loss in _RP_CLUTTER_CLASSES:
            fh.write(f"{code}\t{name}\t{loss:.1f}\n")


def _write_qgs_project(out_dir: Path, bbox) -> Path:
    qgs = out_dir / "radioplanner_import.qgs"
    qgs.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<qgis projectname="TELECOM-TOWER-POWER → RadioPlanner export" version="3.34.0">
  <title>TTP RadioPlanner Export</title>
  <projectCrs><spatialrefsys><srid>4326</srid></spatialrefsys></projectCrs>
  <mapcanvas>
    <extent>
      <xmin>{bbox[0]}</xmin><ymin>{bbox[1]}</ymin>
      <xmax>{bbox[2]}</xmax><ymax>{bbox[3]}</ymax>
    </extent>
  </mapcanvas>
  <layers>
    <maplayer type="vector" name="sites">
      <datasource>file:./sites.csv?delimiter=,&amp;xField=Longitude&amp;yField=Latitude</datasource>
    </maplayer>
    <maplayer type="raster" name="terrain"><datasource>./terrain.asc</datasource></maplayer>
    <maplayer type="raster" name="clutter"><datasource>./clutter.asc</datasource></maplayer>
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
                   help="Skip terrain.asc / clutter.asc (faster, CSV-only export)")
    p.add_argument("--raster-step-deg", type=float, default=1.0 / 1200.0,
                   help="Grid spacing for terrain/clutter ASC (default 3 arc-sec ≈ 90 m)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)

    towers = _load_towers(args.towers_csv, args.towers_from_db, args.aoi_bbox)
    logger.info("loaded %d towers", len(towers))

    n_sites = _write_sites_csv(towers, args.out_dir / "sites.csv")
    n_tx = _write_transmitters_csv(towers, args.out_dir / "transmitters.csv")
    _write_clutter_legend(args.out_dir / "clutter_legend.txt")
    logger.info("wrote %d sites and %d transmitter rows", n_sites, n_tx)

    if not args.skip_rasters:
        try:
            tr = _export_terrain_asc(
                args.aoi_bbox, args.out_dir / "terrain.asc", args.raster_step_deg,
            )
            cl = _export_clutter_asc(
                args.aoi_bbox, args.out_dir / "clutter.asc", args.raster_step_deg,
            )
            logger.info("wrote terrain.asc (%dx%d) and clutter.asc (%dx%d)",
                        tr[0], tr[1], cl[0], cl[1])
        except Exception:
            logger.warning("raster export failed; CSV files still emitted",
                           exc_info=True)

    _write_qgs_project(args.out_dir, args.aoi_bbox)
    logger.info("done — open %s in QGIS to verify before importing into RadioPlanner",
                args.out_dir / "radioplanner_import.qgs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
