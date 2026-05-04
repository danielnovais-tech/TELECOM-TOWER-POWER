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
from typing import List, Optional, Tuple

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


def _emit_manifest(
    *,
    aoi_name: str,
    bbox: BoundingBox,
    frequencies_hz: Tuple[float, ...],
    out_dir: str,
) -> dict:
    """Compute the manifest dict and write it to ``out_dir``.

    The manifest is the *only* artefact this scaffold writes today.
    """
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
        "buildings_count": None,
        "terrain_source": None,
        "clutter_source": None,
        "buildings_geojson_sha256": None,
        "terrain_tif_sha256": None,
        "scene_xml_sha256": None,
        "implementation_status": "scaffold",
        "notes": (
            "scaffold-only build: scene.xml NOT emitted, the GPU worker "
            "will refuse to trace against this manifest "
            "(implementation_status != 'complete')."
        ),
    }
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
    args = p.parse_args(argv)

    frequencies_hz = _frequency_list(args.frequencies)

    if not args.allow_stub:
        sys.stderr.write(
            "ERROR: scene-builder is a scaffold; pass --allow-stub to emit "
            "the manifest only. Wiring the OSM/SRTM/clutter pipelines is "
            "tracked in docs/rf-engines.md § Q2/2026 delivery checklist.\n"
        )
        return 2

    _emit_manifest(
        aoi_name=args.aoi_name,
        bbox=args.bbox,
        frequencies_hz=frequencies_hz,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
