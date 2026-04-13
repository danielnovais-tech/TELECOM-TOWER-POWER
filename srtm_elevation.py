"""
srtm_elevation.py
Read SRTM .hgt files (SRTM3: 3 arc-sec ~90 m resolution) for offline
elevation lookup.

Download tiles from:
  https://dds.cr.usgs.gov/srtm/version2_1/SRTM3/

Place them in ./srtm_data/ with standard naming:
  N15W048.hgt, S16W048.hgt, etc.
"""

import logging
import math
import os
import struct
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from typing import List, Optional, Tuple

try:
    import redis as _redis_mod
except ImportError:
    _redis_mod = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# SRTM3 tiles are 1201 x 1201 samples (3 arc-second spacing)
_SRTM3_SAMPLES = 1201
_VOID = -32768
_TILE_BYTES = _SRTM3_SAMPLES * _SRTM3_SAMPLES * 2


class SRTMReader:
    """Thread-safe, lazy-loading SRTM .hgt reader with in-memory tile cache.

    When ``redis_url`` is provided (or the ``SRTM_REDIS_URL`` env-var is
    set), parsed tiles are also cached in Redis as raw binary blobs.  This
    eliminates disk seeks on spinning drives and allows multiple workers
    to share a single warm cache.
    """

    def __init__(self, data_dir: str = "./srtm_data", redis_url: Optional[str] = None):
        self.data_dir = data_dir
        self._tile_cache: dict[str, list[list[int]]] = {}

        # Optional Redis L2 cache
        _url = redis_url or os.getenv("SRTM_REDIS_URL")
        self._redis: Optional[object] = None
        if _url and _redis_mod is not None:
            try:
                self._redis = _redis_mod.Redis.from_url(_url, decode_responses=False)
                self._redis.ping()  # type: ignore[union-attr]
                logger.info("SRTM Redis cache enabled at %s", _url)
            except Exception:
                logger.warning("Failed to connect to Redis at %s; falling back to disk", _url)
                self._redis = None

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
    # Tile downloading & pre-fetching
    # ------------------------------------------------------------------

    # SRTM3 data is split into continental regions on the USGS server.
    _SRTM3_REGIONS = [
        "Africa", "Australia", "Eurasia",
        "Islands", "North_America", "South_America",
    ]
    _SRTM3_BASE_URL = (
        "https://dds.cr.usgs.gov/srtm/version2_1/SRTM3"
    )

    def tiles_for_bounds(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
    ) -> List[str]:
        """Return a list of tile names covering the bounding box."""
        tiles: List[str] = []
        lat_start = math.floor(min_lat)
        lat_end = math.floor(max_lat)
        lon_start = math.floor(min_lon)
        lon_end = math.floor(max_lon)
        for lat_deg in range(lat_start, lat_end + 1):
            for lon_deg in range(lon_start, lon_end + 1):
                # SRTM coverage: 60°S to 60°N
                if lat_deg < -60 or lat_deg >= 60:
                    continue
                tiles.append(self._tile_name(float(lat_deg), float(lon_deg)))
        return tiles

    def missing_tiles(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
    ) -> List[str]:
        """Return tile names in the bounding box that are not on disk."""
        needed = self.tiles_for_bounds(min_lat, min_lon, max_lat, max_lon)
        return [
            t for t in needed
            if not os.path.isfile(os.path.join(self.data_dir, t))
        ]

    def download_tile(self, tile_name: str) -> bool:
        """
        Download a single .hgt tile from the USGS SRTM3 server.

        Tries each continental region until a match is found.  The server
        stores tiles as *{tile}.hgt.zip*, so we download and extract.

        Returns True on success, False if the tile was not found in any
        region.
        """
        dest = os.path.join(self.data_dir, tile_name)
        if os.path.isfile(dest):
            return True

        os.makedirs(self.data_dir, exist_ok=True)

        for region in self._SRTM3_REGIONS:
            url = f"{self._SRTM3_BASE_URL}/{region}/{tile_name}.zip"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "SRTM-Prefetch/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                with zipfile.ZipFile(BytesIO(data)) as zf:
                    for member in zf.namelist():
                        if member.endswith(".hgt"):
                            zf.extract(member, self.data_dir)
                            logger.info("Downloaded %s from %s", tile_name, region)
                            return True
            except (urllib.error.HTTPError, urllib.error.URLError):
                continue
            except zipfile.BadZipFile:
                logger.warning("Bad ZIP for %s from %s", tile_name, region)
                continue
        logger.warning("Tile %s not found on USGS server", tile_name)
        return False

    def prefetch_bounds(
        self,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        *,
        on_progress: Optional[object] = None,
    ) -> Tuple[int, int]:
        """
        Download all missing tiles for a bounding box.

        *on_progress*, if given, is called as ``on_progress(downloaded, total)``
        after each tile attempt.

        Returns ``(downloaded_ok, total_missing)`` counts.
        """
        missing = self.missing_tiles(min_lat, min_lon, max_lat, max_lon)
        total = len(missing)
        ok = 0
        for idx, tile in enumerate(missing, 1):
            if self.download_tile(tile):
                ok += 1
            if on_progress is not None:
                on_progress(idx, total)
        logger.info(
            "Prefetch complete: %d/%d tiles downloaded for "
            "bounds (%.1f,%.1f)→(%.1f,%.1f)",
            ok, total, min_lat, min_lon, max_lat, max_lon,
        )
        return ok, total

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

        # L2: try Redis before hitting disk
        if self._redis is not None:
            try:
                blob = self._redis.get(f"srtm:{name}")  # type: ignore[union-attr]
                if blob is not None and len(blob) == _TILE_BYTES:
                    tile = self._parse_tile_bytes(blob)
                    self._tile_cache[name] = tile
                    return tile
            except Exception:
                logger.debug("Redis read failed for %s; falling back to disk", name)

        path = self._tile_path(lat, lon)
        if path is None:
            return None

        raw = self._read_hgt_bytes(path)
        tile = self._parse_tile_bytes(raw)
        self._tile_cache[name] = tile

        # Store in Redis for other workers (TTL: 7 days)
        if self._redis is not None:
            try:
                self._redis.set(f"srtm:{name}", raw, ex=7 * 86400)  # type: ignore[union-attr]
            except Exception:
                logger.debug("Redis write failed for %s", name)

        return tile

    @staticmethod
    def _read_hgt_bytes(path: str) -> bytes:
        """Read raw bytes from a .hgt file."""
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) != _TILE_BYTES:
            raise ValueError(
                f"{path}: expected {_TILE_BYTES} bytes, got {len(data)}"
            )
        return data

    @staticmethod
    def _parse_tile_bytes(data: bytes) -> list[list[int]]:
        """Parse raw .hgt bytes into a 2-D list of signed int16 elevations."""
        tile: list[list[int]] = []
        for r in range(_SRTM3_SAMPLES):
            offset = r * _SRTM3_SAMPLES * 2
            row_bytes = data[offset : offset + _SRTM3_SAMPLES * 2]
            row = list(struct.unpack(f">{_SRTM3_SAMPLES}h", row_bytes))
            row = [0 if v == _VOID else v for v in row]
            tile.append(row)
        return tile
