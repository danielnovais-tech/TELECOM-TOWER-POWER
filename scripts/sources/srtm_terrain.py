# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""SRTM3 → GeoTIFF terrain crop for the scene-builder data phase.

Samples ``srtm_elevation.SRTMReader`` on a regular geographic grid
covering the AOI bbox and writes a single-band Float32 GeoTIFF in
EPSG:4326.

Resolution defaults to 3 arc-seconds (≈ 90 m at the equator) — the
native SRTM3 grid. The caller can request a finer step but every
sample is still bilinearly interpolated from the same underlying
1201×1201 tile, so over-sampling above 3″ buys no real detail; we
allow it because some downstream Mitsuba pipelines prefer a 1″
power-of-two grid.

Refuses to run when **any** required tile is missing on disk —
``--prefetch-srtm`` lifts that gate by attempting USGS downloads
through ``SRTMReader.prefetch_bounds`` first.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Native SRTM3 grid step in degrees (3 arc-seconds).
_SRTM3_STEP_DEG = 1.0 / 1200.0
DEFAULT_GRID_STEP_DEG = _SRTM3_STEP_DEG

# Sentinel for SRTM voids that the reader could not interpolate. We
# write -32768 to keep parity with the upstream SRTM convention so
# downstream Mitsuba scene builders can mask them with a single test.
TERRAIN_NODATA = -32768.0


def _grid_dims(
    bbox: Tuple[float, float, float, float], step_deg: float,
) -> Tuple[int, int]:
    south, west, north, east = bbox
    height = int(math.ceil((north - south) / step_deg)) + 1
    width = int(math.ceil((east - west) / step_deg)) + 1
    return height, width


def sample_grid(
    reader,  # type: ignore[no-untyped-def]
    bbox: Tuple[float, float, float, float],
    *,
    step_deg: float = DEFAULT_GRID_STEP_DEG,
) -> np.ndarray:
    """Return a Float32 ``(height, width)`` array of metres.

    ``reader`` is anything with a ``get_elevation(lat, lon) -> Optional[float]``
    method — the production case is ``srtm_elevation.SRTMReader``; tests
    pass a stub.
    """
    south, west, north, east = bbox
    height, width = _grid_dims(bbox, step_deg)
    grid = np.full((height, width), TERRAIN_NODATA, dtype=np.float32)
    voids = 0
    for row in range(height):
        # Row 0 = north edge (matches GeoTIFF convention with negative
        # north-south pixel size).
        lat = north - row * step_deg
        for col in range(width):
            lon = west + col * step_deg
            elev = reader.get_elevation(lat, lon)
            if elev is None:
                voids += 1
                continue
            grid[row, col] = float(elev)
    if voids:
        logger.warning(
            "terrain grid has %d/%d void pixels (%.1f%%) — operator should "
            "fetch missing SRTM tiles before promoting this scene",
            voids, height * width, 100.0 * voids / (height * width),
        )
    return grid


def write_geotiff(
    grid: np.ndarray,
    bbox: Tuple[float, float, float, float],
    *,
    step_deg: float,
    path: str,
) -> None:
    """Write the terrain grid to ``path`` as an EPSG:4326 Float32 GeoTIFF."""
    try:
        import rasterio  # type: ignore[import-not-found]
        from rasterio.transform import from_origin  # type: ignore[import-not-found]
    except ImportError as ex:  # pragma: no cover — pinned in requirements.txt
        raise RuntimeError(
            "rasterio is required to write terrain.tif. "
            "Install requirements.txt (rasterio>=1.3,<2.0)."
        ) from ex
    south, west, north, east = bbox
    transform = from_origin(west, north, step_deg, step_deg)
    profile = {
        "driver": "GTiff",
        "height": grid.shape[0],
        "width": grid.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": TERRAIN_NODATA,
        "compress": "deflate",
        "predictor": 3,  # floating-point predictor — best for elevation
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(grid, 1)
    logger.info("wrote %s (%dx%d, %s pixels void=%d)",
                path, grid.shape[1], grid.shape[0], grid.dtype,
                int((grid == TERRAIN_NODATA).sum()))


def summarise(grid: np.ndarray) -> dict:
    """Manifest summary: min/max/mean elevation + void fraction."""
    valid = grid[grid != TERRAIN_NODATA]
    if valid.size == 0:
        return {
            "shape": list(grid.shape),
            "void_fraction": 1.0,
            "elev_min_m": None,
            "elev_max_m": None,
            "elev_mean_m": None,
        }
    return {
        "shape": list(grid.shape),
        "void_fraction": round(
            float((grid == TERRAIN_NODATA).sum()) / float(grid.size), 4
        ),
        "elev_min_m": float(np.min(valid)),
        "elev_max_m": float(np.max(valid)),
        "elev_mean_m": round(float(np.mean(valid)), 2),
    }
