# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""scripts/build_mitsuba_scene.py — OSM/SRTM/clutter → Mitsuba 3 scene.

Roadmap Q2/2026 — **CLI scaffold only.** This script defines the
contract (CLI flags, output layout, AOI handling) so downstream
infrastructure (S3 layout, Batch job definition, scene cache) can be
provisioned ahead of the actual implementation. Heavy lifting
(Overpass query → triangulated meshes → ITU-R P.2040 material
tagging → Mitsuba XML emission) lands incrementally as the GPU
worker pool comes online.

Output layout (under ``$OUT_DIR``)::

    <aoi-name>/
      scene.xml             Mitsuba 3 scene description
      materials.json        Per-material P.2040 permittivity sidecar
      buildings.geojson     Source OSM footprints (provenance)
      terrain.tif           SRTM tile crop (provenance)
      manifest.json         AOI bbox, build timestamp, source hashes,
                            scene-builder git rev, P.2040 table version

The ``manifest.json`` is the source of truth that lets the GPU worker
refuse to launch a trace against a scene built with a stale material
table — that's the single most likely silent-corruption mode for
mmWave predictions.

Usage (current — emits a stub manifest, refuses to write XML)::

    python scripts/build_mitsuba_scene.py \\
        --aoi-name sp-centro \\
        --bbox -23.560,-46.660,-23.540,-46.620 \\
        --frequencies 28e9,39e9 \\
        --out-dir s3://telecom-tower-power-scenes/dev/

Usage (planned, post-Q2/2026)::

    # Same flags; will actually emit scene.xml.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("build_mitsuba_scene")

# Pinned ITU-R P.2040 table version. Bumping this string forces a
# scene-cache invalidation downstream — that's intentional. Keep it
# tied to the exact recommendation revision the material library was
# extracted from.
P2040_TABLE_VERSION = "P.2040-3 (2023-09)"

# Default frequencies the manifest assumes a scene was prepared for.
# A trace at a frequency outside this set is allowed but logs a
# warning — the worker uses linear interpolation in log-space when
# the requested f_hz falls between tabulated values.
_DEFAULT_FREQUENCIES_HZ = (28e9, 39e9, 60e9)


@dataclass
class BoundingBox:
    """Axis-aligned WGS84 bbox.

    Convention: south-west / north-east corners. Stored in degrees.
    """

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.south < self.north <= 90.0):
            raise ValueError(
                f"invalid latitude range: south={self.south}, north={self.north}"
            )
        if not (-180.0 <= self.west < self.east <= 180.0):
            raise ValueError(
                f"invalid longitude range: west={self.west}, east={self.east}"
            )
        # Soft cap — Overpass refuses queries above ~25 km², and a
        # full-3D mmWave trace at 1 m grid is ~30 GPU-min/km² on a
        # G5.2xlarge. Above 25 km² the operator is asking for
        # something that will not finish before SQS visibility timeout.
        area_deg2 = (self.north - self.south) * (self.east - self.west)
        if area_deg2 > 0.05:  # ≈ 25 km² near the equator
            raise ValueError(
                f"AOI too large for a single scene build: {area_deg2:.3f} deg² "
                "(soft limit 0.05 deg² ≈ 25 km²). Tile the build first."
            )

    @classmethod
    def parse(cls, raw: str) -> "BoundingBox":
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise argparse.ArgumentTypeError(
                "bbox must be 'south,west,north,east' in WGS84 degrees"
            )
        try:
            s, w, n, e = (float(p) for p in parts)
        except ValueError as ex:
            raise argparse.ArgumentTypeError(f"bbox parse failed: {ex}") from ex
        return cls(south=s, west=w, north=n, east=e)


def _git_rev() -> str:
    """Best-effort scene-builder commit hash for the manifest.

    Falls back to ``unknown`` so the script stays usable inside the
    Batch container, where ``.git`` is not copied.
    """
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("ascii").strip()
    except Exception:
        return "unknown"


def _frequency_list(raw: str) -> Tuple[float, ...]:
    if not raw:
        return _DEFAULT_FREQUENCIES_HZ
    out: List[float] = []
    for p in raw.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            f = float(p)
        except ValueError as ex:
            raise argparse.ArgumentTypeError(
                f"could not parse frequency '{p}' as float Hz"
            ) from ex
        if f <= 0:
            raise argparse.ArgumentTypeError(f"non-positive frequency: {f}")
        out.append(f)
    return tuple(out)


def _sha256_file(path: str) -> str:
    """SHA-256 of the file contents — recorded in the manifest."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit_manifest(
    *,
    aoi_name: str,
    bbox: BoundingBox,
    frequencies_hz: Tuple[float, ...],
    out_dir: str,
    extras: Optional[Dict[str, Any]] = None,
    implementation_status: str = "scaffold",
    notes: Optional[str] = None,
) -> dict:
    """Compute the manifest dict and write it to ``out_dir``.

    ``extras`` overrides the optional schema fields filled in by the
    data-source phase (``buildings_count``, ``buildings_geojson_sha256``,
    ``terrain_source``, ``terrain_tif_sha256`` and the per-source
    summaries). ``implementation_status`` is ``"scaffold"`` for the
    manifest-only path and ``"data-only"`` once the buildings/terrain
    artefacts have been written. ``"complete"`` is reserved for the
    Mitsuba-XML phase landing in Tijolo 4.
    """
    extras = extras or {}
    if notes is None:
        notes = (
            "scaffold-only build: scene.xml NOT emitted, the GPU worker "
            "will refuse to trace against this manifest "
            "(implementation_status != 'complete')."
        )
    manifest = {
        "schema_version": 1,
        "aoi_name": aoi_name,
        "bbox": asdict(bbox),
        "frequencies_hz": list(frequencies_hz),
        "p2040_table_version": P2040_TABLE_VERSION,
        "scene_builder_git_rev": _git_rev(),
        "built_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        # Empty until the actual builder phases land. Keeping the
        # fields present in the schema lets the worker's manifest
        # validator stay stable across the rollout.
        "buildings_count": extras.get("buildings_count"),
        "terrain_source": extras.get("terrain_source"),
        "clutter_source": extras.get("clutter_source"),
        "buildings_geojson_sha256": extras.get("buildings_geojson_sha256"),
        "terrain_tif_sha256": extras.get("terrain_tif_sha256"),
        "scene_xml_sha256": extras.get("scene_xml_sha256"),
        "implementation_status": implementation_status,
        "notes": notes,
    }
    if extras.get("buildings_summary") is not None:
        manifest["buildings_summary"] = extras["buildings_summary"]
    if extras.get("terrain_summary") is not None:
        manifest["terrain_summary"] = extras["terrain_summary"]
    if extras.get("materials_p2040") is not None:
        manifest["materials_p2040"] = extras["materials_p2040"]
    for k in ("buildings_mesh_count", "buildings_mesh_vertices",
              "buildings_mesh_faces", "buildings_ply_sha256",
              "terrain_ply_sha256", "reference_frequency_hz"):
        if extras.get(k) is not None:
            manifest[k] = extras[k]
    if out_dir.startswith("s3://"):
        # Defensive: don't silently no-op an S3 push from the scaffold.
        # The intended uploader is the AWS Batch container, not a dev
        # laptop — print the JSON and the object key the implementation
        # would write to.
        target = f"{out_dir.rstrip('/')}/{aoi_name}/manifest.json"
        logger.info("would upload manifest to %s", target)
        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
        return manifest

    aoi_dir = os.path.join(out_dir, aoi_name)
    os.makedirs(aoi_dir, exist_ok=True)
    manifest_path = os.path.join(aoi_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    logger.info("wrote %s", manifest_path)
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="Build a Mitsuba 3 scene for Sionna RT (Q2/2026 scaffold)",
    )
    p.add_argument("--aoi-name", required=True, help="short slug, e.g. sp-centro")
    p.add_argument("--bbox", required=True, type=BoundingBox.parse,
                   help="south,west,north,east in WGS84 degrees")
    p.add_argument("--frequencies", default="",
                   help="comma-separated centre frequencies in Hz "
                        "(default 28e9,39e9,60e9)")
    p.add_argument("--out-dir", required=True,
                   help="local path or s3:// URI for the AOI scene bundle")
    p.add_argument("--allow-stub", action="store_true",
                   help="emit only manifest.json — scene.xml stays unwritten "
                        "(default behaviour today; required until the Q2/2026 "
                        "scene-builder phases land)")
    p.add_argument("--fetch-data", action="store_true",
                   help="Tijolo 2: fetch OSM building footprints (Overpass) "
                        "+ SRTM terrain into <out-dir>/<aoi>/buildings.geojson "
                        "and terrain.tif. Manifest implementation_status "
                        "becomes 'data-only'. Mutually exclusive with "
                        "--allow-stub.")
    p.add_argument("--overpass-url", default=None,
                   help="Override the Overpass endpoint (defaults to the "
                        "public rotation; set this to a private mirror for "
                        "production builds).")
    p.add_argument("--srtm-data-dir", default="./srtm_data",
                   help="Directory containing SRTM3 .hgt tiles (default "
                        "./srtm_data — same as srtm_elevation.SRTMReader).")
    p.add_argument("--prefetch-srtm", action="store_true",
                   help="Download missing SRTM tiles from USGS before "
                        "sampling. Off by default to keep the build offline-"
                        "safe.")
    p.add_argument("--terrain-step-deg", type=float, default=None,
                   help="Terrain grid step in degrees (default 1/1200 ≈ 3″, "
                        "the native SRTM3 resolution).")
    p.add_argument("--emit-scene", action="store_true",
                   help="Tijolo 4: after the data phase, extrude building "
                        "footprints to triangulated meshes (buildings.ply), "
                        "emit a flat ground plane (terrain.ply) and write "
                        "scene.xml with Sionna 'radio-material' BSDFs from "
                        "the P.2040-3 library. Implies --fetch-data.")
    p.add_argument("--reference-frequency-hz", type=float, default=28e9,
                   help="Frequency stamped into the scene.xml radio-material "
                        "BSDFs (default 28 GHz; Sionna RT recomputes per-"
                        "trace, this is just the XML-time value).")
    args = p.parse_args(argv)

    frequencies_hz = _frequency_list(args.frequencies)

    if args.allow_stub and (args.fetch_data or args.emit_scene):
        sys.stderr.write(
            "ERROR: --allow-stub is mutually exclusive with --fetch-data "
            "and --emit-scene.\n"
        )
        return 2
    if args.emit_scene and not args.fetch_data:
        # --emit-scene implies --fetch-data; flag it on for the caller
        # rather than failing.
        args.fetch_data = True
    if not args.allow_stub and not args.fetch_data:
        sys.stderr.write(
            "ERROR: scene-builder is a scaffold; pass --allow-stub to emit "
            "the manifest only, --fetch-data for the Tijolo 2 data-source "
            "phase, or --emit-scene for the full Tijolo 4 build (data + "
            "scene.xml). Tracked in docs/rf-engines.md § Q2/2026 delivery "
            "checklist.\n"
        )
        return 2

    if args.allow_stub:
        _emit_manifest(
            aoi_name=args.aoi_name,
            bbox=args.bbox,
            frequencies_hz=frequencies_hz,
            out_dir=args.out_dir,
        )
        return 0

    return _run_data_phase(args, frequencies_hz)


def _run_data_phase(args, frequencies_hz: Tuple[float, ...]) -> int:
    """Tijolo 2: fetch Overpass + SRTM and emit a data-only manifest.

    Local out-dir only. S3 staging is the next phase's job (the
    AWS Batch container will run this same code with a local out-dir
    and then sync to S3 with checksums).
    """
    if args.out_dir.startswith("s3://"):
        sys.stderr.write(
            "ERROR: --fetch-data writes large binary artefacts; point "
            "--out-dir at a local path. The S3 staging step lives in the "
            "Batch entrypoint, not in this script.\n"
        )
        return 2

    # Lazy imports — keep the manifest-only path free of numpy/rasterio.
    from scripts.sources import (
        overpass_buildings,
        p2040_materials,
        srtm_terrain,
    )

    bbox = args.bbox
    bbox_tuple = (bbox.south, bbox.west, bbox.north, bbox.east)
    aoi_dir = os.path.join(args.out_dir, args.aoi_name)
    os.makedirs(aoi_dir, exist_ok=True)

    # 1) Overpass → buildings.geojson
    logger.info("phase 1/2: Overpass building footprints")
    overpass_url = (
        args.overpass_url
        or os.environ.get("OVERPASS_URL")
        or overpass_buildings.DEFAULT_OVERPASS_URL
    )
    geojson = overpass_buildings.fetch_buildings(
        bbox_tuple, overpass_url=overpass_url,
    )
    buildings_path = os.path.join(aoi_dir, "buildings.geojson")
    with open(buildings_path, "w", encoding="utf-8") as fh:
        json.dump(geojson, fh)
        fh.write("\n")
    buildings_summary = overpass_buildings.summarise(geojson)
    logger.info("wrote %s (%d buildings)",
                buildings_path, buildings_summary["count"])

    # 2) SRTM → terrain.tif
    logger.info("phase 2/2: SRTM terrain crop")
    # Import locally so a missing srtm_elevation (unlikely) doesn't
    # poison the manifest-only path.
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from srtm_elevation import SRTMReader  # type: ignore[import-not-found]

    reader = SRTMReader(data_dir=args.srtm_data_dir)
    if args.prefetch_srtm:
        missing = reader.missing_tiles(
            bbox.south, bbox.west, bbox.north, bbox.east,
        )
        for tile in missing:
            logger.info("prefetching SRTM tile %s", tile)
            reader.download_tile(tile)
    step = args.terrain_step_deg or srtm_terrain.DEFAULT_GRID_STEP_DEG
    grid = srtm_terrain.sample_grid(reader, bbox_tuple, step_deg=step)
    terrain_path = os.path.join(aoi_dir, "terrain.tif")
    srtm_terrain.write_geotiff(
        grid, bbox_tuple, step_deg=step, path=terrain_path,
    )
    terrain_summary = srtm_terrain.summarise(grid)

    # 3) ITU-R P.2040-3 material library — hash + per-material evaluations
    # at the requested frequencies. Recording the SHA-256 lets the GPU
    # worker refuse to trace a scene built against a stale material set.
    materials_lib = p2040_materials.load_library()
    materials_eval = p2040_materials.evaluate_all(
        materials_lib, frequencies_hz,
    )
    materials_block = {
        "library_path": "data/materials_p2040.json",
        "library_sha256": p2040_materials.library_sha256(),
        "table_version": materials_lib["table_version"],
        "materials": materials_eval,
    }

    extras: Dict[str, Any] = {
        "buildings_count": buildings_summary["count"],
        "buildings_geojson_sha256": _sha256_file(buildings_path),
        "buildings_summary": buildings_summary,
        "terrain_source": "SRTM3 (USGS v2.1)",
        "terrain_tif_sha256": _sha256_file(terrain_path),
        "terrain_summary": terrain_summary,
        "clutter_source": None,
        "materials_p2040": materials_block,
    }

    if getattr(args, "emit_scene", False):
        return _run_scene_phase(
            args=args,
            frequencies_hz=frequencies_hz,
            aoi_dir=aoi_dir,
            bbox_tuple=bbox_tuple,
            geojson=geojson,
            terrain_summary=terrain_summary,
            materials_eval=materials_eval,
            extras=extras,
        )

    _emit_manifest(
        aoi_name=args.aoi_name,
        bbox=bbox,
        frequencies_hz=frequencies_hz,
        out_dir=args.out_dir,
        extras=extras,
        implementation_status="data-only",
        notes=(
            "data-only build: buildings.geojson + terrain.tif written from "
            "Overpass + SRTM3. scene.xml NOT emitted (Tijolo 4); the GPU "
            "worker still refuses to trace this manifest."
        ),
    )
    return 0


def _run_scene_phase(
    *,
    args,
    frequencies_hz: Tuple[float, ...],
    aoi_dir: str,
    bbox_tuple: Tuple[float, float, float, float],
    geojson: Dict[str, Any],
    terrain_summary: Dict[str, Any],
    materials_eval: Dict[str, Any],
    extras: Dict[str, Any],
) -> int:
    """Tijolo 4: extrude footprints → PLY meshes + scene.xml.

    Runs after ``_run_data_phase`` has produced ``buildings.geojson``
    and ``terrain.tif``. The output is a self-contained Mitsuba 3
    bundle that Sionna RT 1.x / 2.x can load directly.
    """
    from scripts.sources import mitsuba_scene

    # 4a) Buildings → PLY mesh.
    logger.info("phase 3/4: extruding building footprints → buildings.ply")
    verts, faces, mesh_count = mitsuba_scene.buildings_to_mesh(
        geojson, bbox_tuple, ground_z_m=0.0,
    )
    if mesh_count == 0:
        sys.stderr.write(
            "ERROR: no buildings produced a valid mesh; refusing to emit "
            "an empty scene.xml.\n"
        )
        return 2
    buildings_ply_path = os.path.join(aoi_dir, "buildings.ply")
    mitsuba_scene.write_ply_binary(verts, faces, buildings_ply_path)
    logger.info("wrote %s (%d buildings, %d vertices, %d faces)",
                buildings_ply_path, mesh_count, len(verts), len(faces))

    # 4b) Terrain plane.
    logger.info("phase 4/4: terrain plane + scene.xml")
    elev_mean = terrain_summary.get("elev_mean_m")
    if elev_mean is None:
        # All-void SRTM grid. Default to 0 m and warn — the trace will
        # treat the scene as sea level, which is wrong but visible.
        logger.warning(
            "terrain grid is fully void; placing ground plane at z=0 m"
        )
        elev_mean = 0.0
    t_verts, t_faces = mitsuba_scene.emit_terrain_plane(
        bbox_tuple, float(elev_mean),
    )
    terrain_ply_path = os.path.join(aoi_dir, "terrain.ply")
    mitsuba_scene.write_ply_binary(t_verts, t_faces, terrain_ply_path)

    scene_xml_path = os.path.join(aoi_dir, "scene.xml")
    mitsuba_scene.emit_scene_xml(
        buildings_ply=buildings_ply_path,
        terrain_ply=terrain_ply_path,
        materials_eval=materials_eval,
        out_path=scene_xml_path,
        reference_frequency_hz=float(args.reference_frequency_hz),
    )

    extras = dict(extras)
    extras["buildings_mesh_count"] = mesh_count
    extras["buildings_mesh_vertices"] = len(verts)
    extras["buildings_mesh_faces"] = len(faces)
    extras["buildings_ply_sha256"] = _sha256_file(buildings_ply_path)
    extras["terrain_ply_sha256"] = _sha256_file(terrain_ply_path)
    extras["scene_xml_sha256"] = _sha256_file(scene_xml_path)
    extras["reference_frequency_hz"] = float(args.reference_frequency_hz)

    _emit_manifest(
        aoi_name=args.aoi_name,
        bbox=args.bbox,
        frequencies_hz=frequencies_hz,
        out_dir=args.out_dir,
        extras=extras,
        implementation_status="complete",
        notes=(
            "complete build: buildings.geojson, terrain.tif, buildings.ply, "
            "terrain.ply, and scene.xml emitted from Overpass + SRTM3 + "
            "P.2040-3 materials. Sionna RT may load this manifest "
            "(implementation_status='complete'). Terrain is a flat plane "
            "at AOI mean elevation — a heightfield emitter is a follow-up."
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
