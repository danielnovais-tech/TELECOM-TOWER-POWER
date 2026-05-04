# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the Tijolo 2 data-source phase of the scene builder.

Network calls and SRTM disk I/O are mocked; the tests verify:

- Overpass response → GeoJSON FeatureCollection with the documented
  height-resolution rules.
- SRTM grid sampling produces a Float32 array with NaN→sentinel.
- ``build_mitsuba_scene.main --fetch-data`` writes
  ``buildings.geojson`` + ``terrain.tif`` + a ``data-only`` manifest
  with non-null SHA-256 fields.
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

import numpy as np
import pytest

# scripts/ is not a package on disk; add to path so imports resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.build_mitsuba_scene import BoundingBox, _emit_manifest, main
from scripts.sources import overpass_buildings, srtm_terrain


_OVERPASS_FIXTURE = {
    "elements": [
        {
            "type": "way",
            "id": 1,
            "tags": {"building": "yes", "height": "12 m"},
            "geometry": [
                {"lat": -23.55, "lon": -46.63},
                {"lat": -23.55, "lon": -46.629},
                {"lat": -23.549, "lon": -46.629},
                {"lat": -23.549, "lon": -46.63},
                {"lat": -23.55, "lon": -46.63},
            ],
        },
        {
            "type": "way",
            "id": 2,
            "tags": {"building": "residential", "building:levels": "4"},
            "geometry": [
                {"lat": -23.5505, "lon": -46.6305},
                {"lat": -23.5505, "lon": -46.6295},
                {"lat": -23.5495, "lon": -46.6295},
                {"lat": -23.5495, "lon": -46.6305},
            ],
        },
        {
            "type": "way",
            "id": 3,
            "tags": {"building": "shed"},
            "geometry": [
                {"lat": -23.551, "lon": -46.631},
                {"lat": -23.551, "lon": -46.630},
                {"lat": -23.550, "lon": -46.630},
                {"lat": -23.550, "lon": -46.631},
            ],
        },
        # Degenerate way — must be skipped.
        {"type": "way", "id": 4, "tags": {}, "geometry": [
            {"lat": 0.0, "lon": 0.0}]},
        # Non-way element — must be skipped.
        {"type": "relation", "id": 99},
    ],
}


# ---------------- overpass_buildings ----------------

def test_parse_height_explicit_m():
    assert overpass_buildings._parse_height({"height": "12 m"}) == 12.0


def test_parse_height_levels_fallback():
    assert overpass_buildings._parse_height({"building:levels": "5"}) == 15.0


def test_parse_height_default_when_missing():
    assert overpass_buildings._parse_height({}) == \
        overpass_buildings.DEFAULT_BUILDING_HEIGHT_M


def test_to_geojson_skips_degenerate_and_non_way():
    fc = overpass_buildings._to_geojson(_OVERPASS_FIXTURE["elements"])
    assert fc["type"] == "FeatureCollection"
    # Three valid building ways out of five elements.
    assert len(fc["features"]) == 3
    sources = [f["properties"]["height_source"] for f in fc["features"]]
    assert sources == ["tag:height", "tag:building_levels", "default"]
    # The second feature was unclosed in the fixture; verify auto-close.
    ring = fc["features"][1]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]


def test_summarise_height_stats():
    fc = overpass_buildings._to_geojson(_OVERPASS_FIXTURE["elements"])
    s = overpass_buildings.summarise(fc)
    assert s["count"] == 3
    assert s["height_min_m"] == 8.0
    assert s["height_max_m"] == 12.0
    assert s["height_sources"] == {
        "tag:height": 1, "tag:building_levels": 1, "default": 1,
    }
    assert s["footprint_area_total_m2"] > 0


def test_fetch_buildings_aoi_too_large():
    with pytest.raises(ValueError, match="exceeds Overpass soft limit"):
        overpass_buildings.fetch_buildings(
            (-24.0, -47.0, -23.0, -46.0),
        )


def test_fetch_buildings_mocked_http():
    body = json.dumps(_OVERPASS_FIXTURE).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    with mock.patch.object(overpass_buildings.urllib.request, "urlopen",
                           return_value=_Resp()) as urlopen:
        fc = overpass_buildings.fetch_buildings(
            (-23.560, -46.660, -23.540, -46.620),
            overpass_url="https://example.test/api",
        )
    assert urlopen.called
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3


# ---------------- srtm_terrain ----------------

class _StubReader:
    """Returns ``int(lat*100 + lon*100)`` as elevation; ``None`` for void."""

    def __init__(self, void_at=None, **_kwargs):
        # Accept arbitrary kwargs (data_dir=, redis_url=) so this stub
        # can drop into ``SRTMReader``'s call sites verbatim.
        self.void_at = void_at or set()

    def get_elevation(self, lat, lon):
        if (round(lat, 4), round(lon, 4)) in self.void_at:
            return None
        return float(int(lat * 100 + lon * 100))


def test_sample_grid_shape_and_dtype():
    bbox = (-23.550, -46.630, -23.548, -46.628)
    grid = srtm_terrain.sample_grid(_StubReader(), bbox, step_deg=0.001)
    assert grid.dtype == np.float32
    # Float rounding in ceil((0.002)/0.001) gives 3, +1 → (4, 4).
    assert grid.shape == (4, 4)
    # Row 0 = north edge, so grid[0,0] = lat=-23.548, lon=-46.630.
    expected = float(int(-23.548 * 100 + -46.630 * 100))
    assert grid[0, 0] == pytest.approx(expected, abs=1.0)


def test_sample_grid_marks_voids():
    bbox = (-23.550, -46.630, -23.549, -46.629)
    voids = {(-23.5500, -46.6300)}  # SW corner
    grid = srtm_terrain.sample_grid(
        _StubReader(void_at=voids), bbox, step_deg=0.001,
    )
    assert (grid == srtm_terrain.TERRAIN_NODATA).any()


def test_write_geotiff_roundtrip(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    bbox = (-23.550, -46.630, -23.548, -46.628)
    grid = srtm_terrain.sample_grid(_StubReader(), bbox, step_deg=0.001)
    path = str(tmp_path / "terrain.tif")
    srtm_terrain.write_geotiff(grid, bbox, step_deg=0.001, path=path)
    assert os.path.exists(path)
    with rasterio.open(path) as src:
        assert src.count == 1
        assert src.crs.to_epsg() == 4326
        assert src.width == grid.shape[1]
        assert src.height == grid.shape[0]
        read_back = src.read(1)
    np.testing.assert_array_equal(read_back, grid)


def test_terrain_summarise_handles_all_void():
    bbox = (-23.550, -46.630, -23.548, -46.628)
    grid = np.full((3, 3), srtm_terrain.TERRAIN_NODATA, dtype=np.float32)
    s = srtm_terrain.summarise(grid)
    assert s["void_fraction"] == 1.0
    assert s["elev_min_m"] is None


# ---------------- main --fetch-data ----------------

def test_main_rejects_combined_flags(capsys, tmp_path):
    rc = main([
        "--aoi-name", "x",
        "--bbox=-23.55,-46.63,-23.54,-46.62",
        "--out-dir", str(tmp_path),
        "--allow-stub",
        "--fetch-data",
    ])
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_main_rejects_s3_with_fetch_data(capsys, tmp_path):
    rc = main([
        "--aoi-name", "x",
        "--bbox=-23.55,-46.63,-23.54,-46.62",
        "--out-dir", "s3://bucket/dev/",
        "--fetch-data",
    ])
    assert rc == 2
    assert "local path" in capsys.readouterr().err


def test_main_fetch_data_writes_full_bundle(tmp_path):
    pytest.importorskip("rasterio")

    body = json.dumps(_OVERPASS_FIXTURE).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    with mock.patch.object(
        overpass_buildings.urllib.request, "urlopen",
        return_value=_Resp(),
    ), mock.patch("srtm_elevation.SRTMReader", _StubReader):
        rc = main([
            "--aoi-name", "sp-mock",
            "--bbox=-23.550,-46.630,-23.548,-46.628",
            "--out-dir", str(tmp_path),
            "--fetch-data",
            "--terrain-step-deg", "0.001",
        ])
    assert rc == 0
    aoi = tmp_path / "sp-mock"
    assert (aoi / "buildings.geojson").exists()
    assert (aoi / "terrain.tif").exists()
    manifest = json.loads((aoi / "manifest.json").read_text())
    assert manifest["implementation_status"] == "data-only"
    assert manifest["buildings_count"] == 3
    assert manifest["terrain_source"] == "SRTM3 (USGS v2.1)"
    assert manifest["buildings_geojson_sha256"] is not None
    assert manifest["terrain_tif_sha256"] is not None
    assert manifest["scene_xml_sha256"] is None
    assert "buildings_summary" in manifest
    assert "terrain_summary" in manifest


# ---------------- BoundingBox (regression) ----------------

def test_bbox_too_large_rejected():
    with pytest.raises(ValueError, match="AOI too large"):
        BoundingBox(south=-24.0, west=-47.0, north=-23.0, east=-46.0)


def test_emit_manifest_default_scaffold(tmp_path):
    bbox = BoundingBox(south=-23.56, west=-46.66, north=-23.54, east=-46.62)
    m = _emit_manifest(
        aoi_name="x",
        bbox=bbox,
        frequencies_hz=(28e9,),
        out_dir=str(tmp_path),
    )
    assert m["implementation_status"] == "scaffold"
    assert m["buildings_count"] is None
