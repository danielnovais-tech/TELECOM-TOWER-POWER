# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Regression tests for `scripts/qgis_to_atoll.py`.

These pin the wire-format that Forsk Atoll's import wizard expects so a
future refactor cannot silently break interop with paying Atoll users:

* `sites.txt` / `transmitters.txt` are tab-separated, latin-1 encoded,
  with the documented header row.
* The default 3-sector layout produces 3 transmitter rows per tower.
* When ``--skip-rasters`` is omitted, terrain.bil + clutter.bil + their
  ENVI .hdr sidecars exist with the byte-counts implied by the grid.
* `atoll_import.qgs` is well-formed XML pointing at the emitted layers.
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.qgis_to_atoll import main as qgis_to_atoll_main


_TOWERS_CSV = (
    "id,name,lat,lon,height_m,frequency_mhz,eirp_dbm,band,ground_elev_m\n"
    "T1,Site-A,-15.80,-47.85,40,850,62,B5,1100\n"
    "T2,Site-B,-15.90,-47.80,30,2100,55,B1,1150\n"
)


def _run(tmp_path: Path, *, skip_rasters: bool) -> Path:
    towers = tmp_path / "towers.csv"
    towers.write_text(_TOWERS_CSV)
    out = tmp_path / "atoll"
    argv = [
        "--aoi-bbox=-48.0,-16.0,-47.5,-15.7",
        f"--out-dir={out}",
        f"--towers-csv={towers}",
        "--raster-step-deg=0.05",
    ]
    if skip_rasters:
        argv.append("--skip-rasters")
    rc = qgis_to_atoll_main(argv)
    assert rc == 0
    return out


def test_sites_and_transmitters_format(tmp_path):
    out = _run(tmp_path, skip_rasters=True)

    sites = (out / "sites.txt").read_text(encoding="latin-1").splitlines()
    assert sites[0] == "Name\tLongitude\tLatitude\tAltitude\tHeight"
    assert len(sites) == 1 + 2  # header + 2 towers
    cols = sites[1].split("\t")
    assert cols[0] == "Site-A"
    assert float(cols[1]) == -47.85
    assert float(cols[2]) == -15.80

    tx = (out / "transmitters.txt").read_text(encoding="latin-1").splitlines()
    assert tx[0].split("\t") == [
        "Site", "CellName", "Frequency_MHz", "EIRP_dBm",
        "Azimuth_deg", "Tilt_deg", "Height_m", "Band",
    ]
    # Default 3-sector layout → 3 rows per tower.
    assert len(tx) == 1 + 2 * 3
    azimuths = sorted({float(line.split("\t")[4]) for line in tx[1:4]})
    assert azimuths == [0.0, 120.0, 240.0]


def test_qgs_project_is_well_formed_xml(tmp_path):
    out = _run(tmp_path, skip_rasters=True)
    root = ET.parse(out / "atoll_import.qgs").getroot()
    assert root.tag == "qgis"
    layer_names = {ml.get("name") for ml in root.iter("maplayer")}
    assert {"sites", "terrain", "clutter"} <= layer_names


def test_raster_bil_sizes_and_headers(tmp_path):
    out = _run(tmp_path, skip_rasters=False)

    # bbox = (-48.0, -16.0, -47.5, -15.7), step = 0.05
    # ncols = round(0.5/0.05) = 10, nrows = round(0.3/0.05) = 6
    assert (out / "terrain.bil").stat().st_size == 6 * 10 * 2  # int16
    assert (out / "clutter.bil").stat().st_size == 6 * 10 * 1  # uint8

    hdr = (out / "terrain.hdr").read_text()
    assert "NROWS          6" in hdr
    assert "NCOLS          10" in hdr
    assert "NBITS          16" in hdr
    assert "PIXELTYPE      SIGNEDINT" in hdr
    assert "LAYOUT         BIL" in hdr

    chdr = (out / "clutter.hdr").read_text()
    assert "NBITS          8" in chdr
    assert "PIXELTYPE      UNSIGNEDINT" in chdr
