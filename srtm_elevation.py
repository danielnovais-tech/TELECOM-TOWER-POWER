"""
srtm_elevation.py
Read SRTM .hgt files (SRTM3: 3 arc-sec ~90 m resolution) for offline
elevation lookup.

Download tiles from:
  https://dds.cr.usgs.gov/srtm/version2_1/SRTM3/

Place them in ./srtm_data/ with standard naming:
  N15W048.hgt, S16W048.hgt, etc.
"""

import math
import os
import struct
from typing import Optional


# SRTM3 tiles are 1201 x 1201 samples (3 arc-second spacing)
_SRTM3_SAMPLES = 1201
_VOID = -32768


class SRTMReader:
    """Thread-safe, lazy-loading SRTM .hgt reader with in-memory tile cache."""

    def __init__(self, data_dir: str = "./srtm_data"):
        self.data_dir = data_dir
        self._tile_cache: dict[str, list[list[int]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_elevation(self, lat: float, lon: float) -> Optional[float]:
        """Return elevation (m) at *lat, lon* or ``None`` if no tile."""
        tile = self._ensure_tile(lat, lon)
        if tile is None:
            return None

        lat_floor = math.floor(lat)
        lon_floor = math.floor(lon)

        # Fractional position inside tile (0..1)
        lat_frac = lat - lat_floor
        lon_frac = lon - lon_floor

        # .hgt rows run north→south; row 0 = north edge of tile
        row = (1.0 - lat_frac) * (_SRTM3_SAMPLES - 1)
        col = lon_frac * (_SRTM3_SAMPLES - 1)

        r0 = int(row)
        c0 = int(col)
        r1 = min(r0 + 1, _SRTM3_SAMPLES - 1)
        c1 = min(c0 + 1, _SRTM3_SAMPLES - 1)

        # Bilinear interpolation
        dr = row - r0
        dc = col - c0
        v00 = tile[r0][c0]
        v01 = tile[r0][c1]
        v10 = tile[r1][c0]
        v11 = tile[r1][c1]

        elev = (
            v00 * (1 - dr) * (1 - dc)
            + v01 * (1 - dr) * dc
            + v10 * dr * (1 - dc)
            + v11 * dr * dc
        )
        return round(elev, 1)

    def available(self, lat: float, lon: float) -> bool:
        """Check whether a tile exists for the given coordinate."""
        return self._tile_path(lat, lon) is not None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _tile_name(lat: float, lon: float) -> str:
        lat_deg = math.floor(lat)
        lon_deg = math.floor(lon)
        ns = "N" if lat_deg >= 0 else "S"
        ew = "E" if lon_deg >= 0 else "W"
        return f"{ns}{abs(lat_deg):02d}{ew}{abs(lon_deg):03d}.hgt"

    def _tile_path(self, lat: float, lon: float) -> Optional[str]:
        name = self._tile_name(lat, lon)
        path = os.path.join(self.data_dir, name)
        return path if os.path.isfile(path) else None

    def _ensure_tile(self, lat: float, lon: float) -> Optional[list[list[int]]]:
        name = self._tile_name(lat, lon)
        if name in self._tile_cache:
            return self._tile_cache[name]

        path = self._tile_path(lat, lon)
        if path is None:
            return None

        tile = self._load_hgt(path)
        self._tile_cache[name] = tile
        return tile

    @staticmethod
    def _load_hgt(path: str) -> list[list[int]]:
        """Parse a raw .hgt file into a 2-D list of signed int16 elevations."""
        expected = _SRTM3_SAMPLES * _SRTM3_SAMPLES * 2
        with open(path, "rb") as fh:
            data = fh.read()

        if len(data) != expected:
            raise ValueError(
                f"{path}: expected {expected} bytes, got {len(data)}"
            )

        tile: list[list[int]] = []
        for r in range(_SRTM3_SAMPLES):
            offset = r * _SRTM3_SAMPLES * 2
            row_bytes = data[offset : offset + _SRTM3_SAMPLES * 2]
            row = list(struct.unpack(f">{_SRTM3_SAMPLES}h", row_bytes))
            # Replace void pixels with 0
            row = [0 if v == _VOID else v for v in row]
            tile.append(row)
        return tile
