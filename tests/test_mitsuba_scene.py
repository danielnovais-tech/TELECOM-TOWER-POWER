# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the Tijolo 4 Mitsuba scene emitter.

Covers projection, ear-clipping, extrusion, PLY round-trip, scene
XML structure, and the end-to-end ``--emit-scene`` build.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import xml.etree.ElementTree as ET
from unittest import mock

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.sources import mitsuba_scene, p2040_materials


# ---------------- projection ----------------

def test_origin_at_centroid():
    bbox = (-23.55, -46.66, -23.53, -46.62)
    lon0, lat0 = mitsuba_scene.aoi_origin(bbox)
    assert lon0 == pytest.approx(-46.64)
    assert lat0 == pytest.approx(-23.54)


def test_projection_origin_is_zero():
    x, y = mitsuba_scene.project_lonlat_to_local(
        -46.64, -23.54, lon0=-46.64, lat0=-23.54,
    )
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)


def test_projection_scale_north_south():
    # 0.001 deg of latitude ≈ 111 m
    _, y = mitsuba_scene.project_lonlat_to_local(
        -46.64, -23.539, lon0=-46.64, lat0=-23.54,
    )
    assert 110.0 < y < 112.0


# ---------------- triangulation ----------------

def test_triangulate_unit_square():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    tris = mitsuba_scene.triangulate_polygon(sq)
    assert len(tris) == 2
    # Every index in range
    assert all(0 <= a < 4 and 0 <= b < 4 and 0 <= c < 4 for (a, b, c) in tris)


def test_triangulate_handles_duplicate_close():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    tris = mitsuba_scene.triangulate_polygon(sq)
    assert len(tris) == 2


def test_triangulate_l_shape():
    """Non-convex L-polygon → 4 triangles."""
    L = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
    tris = mitsuba_scene.triangulate_polygon(L)
    assert len(tris) == 4


def test_triangulate_degenerate_returns_empty():
    assert mitsuba_scene.triangulate_polygon([(0, 0), (1, 1)]) == []
    assert mitsuba_scene.triangulate_polygon([(0, 0)]) == []


# ---------------- extrusion ----------------

def test_extrude_unit_square_box():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    v, f = mitsuba_scene._extrude_footprint(sq, 3.0)
    # 4 bottom + 4 top = 8 vertices
    assert len(v) == 8
    # 2 bottom + 2 top + 4 walls × 2 = 12 triangles
    assert len(f) == 12
    # Top vertices at z=3, bottom at z=0
    z_values = sorted({z for (_, _, z) in v})
    assert z_values == [0.0, 3.0]


def test_extrude_zero_height_returns_empty():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    v, f = mitsuba_scene._extrude_footprint(sq, 0)
    assert v == [] and f == []


def test_extrude_with_offset_shifts_indices():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    _, f = mitsuba_scene._extrude_footprint(sq, 1.0, vertex_offset=100)
    assert all(min(tri) >= 100 for tri in f)


# ---------------- PLY ----------------

def test_ply_roundtrip(tmp_path):
    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    faces = [(0, 1, 2)]
    path = tmp_path / "tri.ply"
    mitsuba_scene.write_ply_binary(verts, faces, str(path))
    blob = path.read_bytes()
    assert blob.startswith(b"ply\n")
    assert b"element vertex 3" in blob
    assert b"element face 1" in blob
    # Find body after end_header
    body = blob.split(b"end_header\n", 1)[1]
    # 3 verts × 12 bytes + 1 face × (1 + 3×4) bytes
    assert len(body) == 3 * 12 + 13
    # Check first vertex = (0, 0, 0)
    x, y, z = struct.unpack("<fff", body[:12])
    assert (x, y, z) == (0.0, 0.0, 0.0)


# ---------------- buildings_to_mesh ----------------

def _square_feature(lon0, lat0, side_deg, height_m, building="yes"):
    lons = [lon0, lon0 + side_deg, lon0 + side_deg, lon0, lon0]
    lats = [lat0, lat0, lat0 + side_deg, lat0 + side_deg, lat0]
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon",
                     "coordinates": [list(zip(lons, lats))]},
        "properties": {"osm_id": 1, "building": building,
                       "height_m": height_m, "height_source": "tag:height"},
    }


def test_buildings_to_mesh_two_squares():
    bbox = (-23.55, -46.66, -23.53, -46.62)
    fc = {
        "type": "FeatureCollection",
        "features": [
            _square_feature(-46.65, -23.545, 0.0001, 10.0),
            _square_feature(-46.64, -23.540, 0.0001, 20.0),
        ],
    }
    v, f, count = mitsuba_scene.buildings_to_mesh(fc, bbox)
    assert count == 2
    assert len(v) == 16  # 8 per box
    assert len(f) == 24  # 12 per box


def test_buildings_to_mesh_skips_zero_height():
    bbox = (-23.55, -46.66, -23.53, -46.62)
    fc = {
        "type": "FeatureCollection",
        "features": [
            _square_feature(-46.65, -23.545, 0.0001, 10.0),
            _square_feature(-46.64, -23.540, 0.0001, 0.0),
        ],
    }
    _, _, count = mitsuba_scene.buildings_to_mesh(fc, bbox)
    assert count == 1


def test_terrain_plane_two_triangles():
    bbox = (-23.55, -46.66, -23.54, -46.65)
    v, f = mitsuba_scene.emit_terrain_plane(bbox, 750.0)
    assert len(v) == 4
    assert len(f) == 2
    assert all(z == 750.0 for (_, _, z) in v)


# ---------------- scene XML ----------------

def test_emit_scene_xml_structure(tmp_path):
    lib = p2040_materials.load_library()
    materials_eval = p2040_materials.evaluate_all(lib, [28e9, 39e9, 60e9])
    # Need stub PLY paths the XML can reference (no need for content).
    bp = tmp_path / "buildings.ply"
    tp = tmp_path / "terrain.ply"
    bp.write_bytes(b"")
    tp.write_bytes(b"")
    out = tmp_path / "scene.xml"
    mitsuba_scene.emit_scene_xml(
        buildings_ply=str(bp),
        terrain_ply=str(tp),
        materials_eval=materials_eval,
        out_path=str(out),
        reference_frequency_hz=28e9,
    )
    tree = ET.parse(str(out))
    root = tree.getroot()
    assert root.tag == "scene"
    assert root.attrib["version"].startswith("3.")
    bsdf_ids = {b.attrib["id"] for b in root.findall("bsdf")}
    assert bsdf_ids >= {"mat_concrete", "mat_glass", "mat_metal",
                        "mat_vegetation"}
    # Concrete BSDF stamped with the 28 GHz value
    concrete = next(b for b in root.findall("bsdf")
                    if b.attrib["id"] == "mat_concrete")
    eps = next(c for c in concrete if c.attrib.get("name") ==
               "relative_permittivity")
    assert float(eps.attrib["value"]) == pytest.approx(5.31, abs=1e-3)
    # Two shape elements, both PLY refs
    shapes = root.findall("shape")
    assert len(shapes) == 2
    assert {s.attrib["id"] for s in shapes} == {"buildings", "terrain"}
    for s in shapes:
        assert s.attrib["type"] == "ply"


def test_emit_scene_xml_picks_closest_freq(tmp_path):
    lib = p2040_materials.load_library()
    materials_eval = p2040_materials.evaluate_all(lib, [28e9, 60e9])
    bp = tmp_path / "buildings.ply"
    tp = tmp_path / "terrain.ply"
    bp.write_bytes(b"")
    tp.write_bytes(b"")
    out = tmp_path / "scene.xml"
    mitsuba_scene.emit_scene_xml(
        buildings_ply=str(bp), terrain_ply=str(tp),
        materials_eval=materials_eval, out_path=str(out),
        reference_frequency_hz=39e9,  # not in the list
    )
    root = ET.parse(str(out)).getroot()
    concrete = next(b for b in root.findall("bsdf")
                    if b.attrib["id"] == "mat_concrete")
    sigma = next(c for c in concrete if c.attrib.get("name") ==
                 "conductivity")
    # Closer to 28 (∆=11) than to 60 (∆=21).
    expected = 0.0326 * (28.0 ** 0.8095)
    assert float(sigma.attrib["value"]) == pytest.approx(expected, abs=1e-3)


# ---------------- main --emit-scene end-to-end ----------------

_OVERPASS_FIXTURE = {
    "elements": [
        {
            "type": "way",
            "id": 1,
            "tags": {"building": "yes", "height": "12 m"},
            "geometry": [
                {"lat": -23.5495, "lon": -46.6595},
                {"lat": -23.5495, "lon": -46.6585},
                {"lat": -23.5485, "lon": -46.6585},
                {"lat": -23.5485, "lon": -46.6595},
                {"lat": -23.5495, "lon": -46.6595},
            ],
        },
        {
            "type": "way",
            "id": 2,
            "tags": {"building": "yes", "building:levels": "5"},
            "geometry": [
                {"lat": -23.5455, "lon": -46.6555},
                {"lat": -23.5455, "lon": -46.6545},
                {"lat": -23.5445, "lon": -46.6545},
                {"lat": -23.5445, "lon": -46.6555},
            ],
        },
    ],
}


class _StubReader:
    def __init__(self, *a, **kw):
        pass

    def get_elevation(self, lat, lon):
        return 750.0

    def missing_tiles(self, *a, **kw):
        return []


def test_main_emit_scene_full_bundle(tmp_path):
    pytest.importorskip("rasterio")
    from scripts import build_mitsuba_scene
    from scripts.sources import overpass_buildings

    body = json.dumps(_OVERPASS_FIXTURE).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    with mock.patch.object(
        overpass_buildings.urllib.request, "urlopen", return_value=_Resp(),
    ), mock.patch("srtm_elevation.SRTMReader", _StubReader):
        rc = build_mitsuba_scene.main([
            "--aoi-name", "sp-test",
            "--bbox=-23.550,-46.660,-23.540,-46.650",
            "--out-dir", str(tmp_path),
            "--emit-scene",
            "--terrain-step-deg", "0.001",
            "--reference-frequency-hz", "28e9",
        ])
    assert rc == 0
    aoi = tmp_path / "sp-test"
    for fname in ("buildings.geojson", "terrain.tif", "buildings.ply",
                  "terrain.ply", "scene.xml", "manifest.json"):
        assert (aoi / fname).exists(), f"missing {fname}"
    manifest = json.loads((aoi / "manifest.json").read_text())
    assert manifest["implementation_status"] == "complete"
    assert manifest["scene_xml_sha256"] is not None
    assert manifest["buildings_ply_sha256"] is not None
    assert manifest["terrain_ply_sha256"] is not None
    assert manifest["buildings_mesh_count"] == 2
    assert manifest["buildings_mesh_vertices"] == 16
    assert manifest["buildings_mesh_faces"] == 24
    assert manifest["reference_frequency_hz"] == 28e9
    # Verify scene.xml parses and references the local PLY filenames.
    root = ET.parse(str(aoi / "scene.xml")).getroot()
    fnames = [el.attrib["value"]
              for el in root.iter("string")
              if el.attrib.get("name") == "filename"]
    assert "buildings.ply" in fnames
    assert "terrain.ply" in fnames


def test_main_rejects_emit_scene_with_allow_stub(capsys, tmp_path):
    from scripts import build_mitsuba_scene
    rc = build_mitsuba_scene.main([
        "--aoi-name", "x",
        "--bbox=-23.55,-46.66,-23.54,-46.62",
        "--out-dir", str(tmp_path),
        "--allow-stub",
        "--emit-scene",
    ])
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err
