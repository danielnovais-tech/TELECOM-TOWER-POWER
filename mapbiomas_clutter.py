# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""MapBiomas land-use / land-cover (LULC) clutter lookup.

Coverage prediction accuracy is dominated by morphology (urban canyon vs
forest vs pasture vs water) once the FSPL term is taken out. SRTM gives
elevation but not what's *on* the ground — for that we sample MapBiomas
Collection 9 (Brazil-wide LULC raster, 30 m resolution).

Public surface
--------------

    from mapbiomas_clutter import MapBiomasExtractor, get_extractor

    ext = get_extractor()                       # singleton
    code  = ext.get_clutter_class(lat, lon)     # int (MapBiomas code) | None
    label = clutter_class_to_label(code)        # "Forest", "Pasture", ...
    onehot = clutter_class_to_onehot(code)      # np.ndarray (10,)

When :data:`MAPBIOMAS_RASTER_PATH` is unset or the file is missing, every
lookup returns ``None`` — callers must treat clutter as an *optional*
feature so the rest of the pipeline keeps working in environments
without the (multi-GB) raster.

Caching
-------

Lookups are coordinate-rounded to ~30 m (4 decimal degrees) and cached.

* Redis (``REDIS_URL``/``MAPBIOMAS_REDIS_URL`` set): shared across ECS
  tasks, 30-day TTL. Same backend pattern as :mod:`hop_cache`.
* In-process LRU (fallback): bounded at 8192 entries.

A failed Redis call demotes silently to the LRU; never raises.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Lazy import — rasterio is heavy and only needed when a raster is configured.
try:  # pragma: no cover - import guard
    import redis as _redis  # type: ignore
except Exception:  # noqa: BLE001
    _redis = None  # type: ignore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Path to a MapBiomas LULC GeoTIFF (single-band uint8). Brazil-wide rasters
#: ship from https://mapbiomas.org/en/download. When unset, lookups return
#: ``None`` (clutter feature disabled — predictor falls back to terrain only).
MAPBIOMAS_RASTER_PATH = os.getenv("MAPBIOMAS_RASTER_PATH", "")
MAPBIOMAS_REDIS_URL = os.getenv("MAPBIOMAS_REDIS_URL") or os.getenv("REDIS_URL")
MAPBIOMAS_REDIS_TTL_S = int(os.getenv("MAPBIOMAS_REDIS_TTL_S", str(30 * 24 * 3600)))
MAPBIOMAS_LRU_MAX = int(os.getenv("MAPBIOMAS_LRU_MAX", "8192"))

_KEY_PREFIX = "ttp:mb:v1:"

# MapBiomas Collection 9 class codes.
# https://brasil.mapbiomas.org/wp-content/uploads/sites/4/2024/01/Legend-Code-Collection-9.pdf
# The 10 most propagation-relevant classes in Brazil. Values *not* in this
# tuple map to the "Other" bucket (one-hot index 9). The order is the
# canonical column order of the one-hot vector — keep stable across
# retrains.
_TOP10_CLUTTER_CLASSES: Tuple[Tuple[int, str], ...] = (
    (3,  "Forest"),                # Forest formation
    (4,  "Savanna"),               # Savanna formation
    (12, "Grassland"),             # Native grassland
    (15, "Pasture"),               # Cultivated pasture
    (21, "Mosaic"),                # Mosaic of agriculture and pasture
    (24, "Urban"),                 # Urban infrastructure
    (25, "Bare"),                  # Other non-vegetated
    (33, "Water"),                 # River / lake / ocean
    (39, "Soybean"),               # Soybean cropland (proxy for cropland)
    (0,  "Other"),                 # Catch-all (incl. unclassified / off-raster)
)

_CLASS_TO_INDEX: Dict[int, int] = {code: i for i, (code, _) in enumerate(_TOP10_CLUTTER_CLASSES)}
_CLASS_TO_LABEL: Dict[int, str] = {code: label for code, label in _TOP10_CLUTTER_CLASSES}
ONE_HOT_DIM: int = len(_TOP10_CLUTTER_CLASSES)
ONE_HOT_FEATURE_NAMES: Tuple[str, ...] = tuple(
    f"clutter_{label.lower()}" for _, label in _TOP10_CLUTTER_CLASSES
)


def clutter_class_to_label(code: Optional[int]) -> str:
    """Return human-readable label for a MapBiomas class code.

    Unknown / ``None`` → ``"Other"``.
    """
    if code is None:
        return "Other"
    return _CLASS_TO_LABEL.get(int(code), "Other")


def clutter_class_to_onehot(code: Optional[int]) -> np.ndarray:
    """One-hot encode a class code into a 10-dim ``float64`` vector.

    Unknown / ``None`` collapses to the "Other" slot (index 9). Returning
    a non-zero vector even for unknowns matters: a zero vector is
    indistinguishable from "feature missing" by a linear model, whereas
    routing to "Other" lets the ridge learn the average residual for
    unsampled morphologies.
    """
    vec = np.zeros(ONE_HOT_DIM, dtype=float)
    if code is None:
        idx = _CLASS_TO_INDEX[0]
    else:
        idx = _CLASS_TO_INDEX.get(int(code), _CLASS_TO_INDEX[0])
    vec[idx] = 1.0
    return vec


# ---------------------------------------------------------------------------
# Cache backends (mirrors hop_cache.py)
# ---------------------------------------------------------------------------

class _LRU:
    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._d: "OrderedDict[str, Optional[int]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, k: str) -> Tuple[bool, Optional[int]]:
        with self._lock:
            if k not in self._d:
                return False, None
            v = self._d[k]
            self._d.move_to_end(k)
            return True, v

    def put(self, k: str, v: Optional[int]) -> None:
        with self._lock:
            self._d[k] = v
            self._d.move_to_end(k)
            while len(self._d) > self._maxsize:
                self._d.popitem(last=False)


_metrics_lock = threading.Lock()
_metrics: Dict[str, int] = {"hits": 0, "misses": 0, "errors": 0, "raster_reads": 0}


def get_metrics() -> Dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def _bump(name: str, n: int = 1) -> None:
    with _metrics_lock:
        _metrics[name] = _metrics.get(name, 0) + n


def _round_key(lat: float, lon: float, year: int) -> str:
    # 4 decimals ≈ 11 m at the equator — finer than MapBiomas' 30 m cell.
    return f"{_KEY_PREFIX}{year}:{round(lat, 4):.4f}:{round(lon, 4):.4f}"


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class MapBiomasExtractor:
    """Sample a MapBiomas LULC GeoTIFF at point coordinates.

    Parameters
    ----------
    raster_path:
        Path to a single-band integer GeoTIFF in geographic coordinates
        (EPSG:4326). When ``None`` or missing on disk, every
        :meth:`get_clutter_class` call returns ``None`` — i.e. the
        extractor is a no-op and the caller should fall back to
        terrain-only features.
    year:
        Year metadata stamped on cache keys; lets the same Redis serve
        multiple LULC vintages without collisions.
    redis_url:
        Override the auto-detected ``REDIS_URL`` / ``MAPBIOMAS_REDIS_URL``
        endpoint. ``None`` → use env vars; explicit ``""`` → disable Redis
        (in-memory LRU only).
    """

    def __init__(
        self,
        raster_path: Optional[str] = None,
        year: int = 2022,
        redis_url: Optional[str] = None,
    ) -> None:
        self._raster_path = raster_path or MAPBIOMAS_RASTER_PATH or None
        self._year = int(year)
        self._lru = _LRU(MAPBIOMAS_LRU_MAX)
        self._redis: Optional[Any] = None
        self._redis_broken = False
        self._dataset: Optional[Any] = None
        self._dataset_lock = threading.Lock()

        url = redis_url if redis_url is not None else MAPBIOMAS_REDIS_URL
        if url and _redis is not None:
            try:
                self._redis = _redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_timeout=0.25,
                    socket_connect_timeout=0.5,
                )
                logger.info(
                    "mapbiomas: Redis cache enabled (ttl=%ds)", MAPBIOMAS_REDIS_TTL_S
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("mapbiomas: Redis init failed (%s); LRU only", exc)
                self._redis = None

        if self._raster_path and not os.path.exists(self._raster_path):
            logger.warning(
                "mapbiomas: raster %s not found — clutter feature disabled",
                self._raster_path,
            )
            self._raster_path = None

    # ------------------------------------------------------------------
    # Lazy raster handle
    # ------------------------------------------------------------------
    def _open(self) -> Optional[Any]:
        if self._raster_path is None:
            return None
        if self._dataset is not None:
            return self._dataset
        with self._dataset_lock:
            if self._dataset is not None:
                return self._dataset
            try:
                import rasterio  # type: ignore  # local import keeps cold-start fast
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mapbiomas: rasterio not available (%s); clutter disabled",
                    exc,
                )
                self._raster_path = None
                return None
            try:
                self._dataset = rasterio.open(self._raster_path)
                logger.info(
                    "mapbiomas: raster opened (%s, crs=%s, size=%dx%d)",
                    self._raster_path,
                    getattr(self._dataset, "crs", "?"),
                    self._dataset.width,
                    self._dataset.height,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mapbiomas: failed to open %s (%s); clutter disabled",
                    self._raster_path,
                    exc,
                )
                self._raster_path = None
                self._dataset = None
        return self._dataset

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _redis_get(self, key: str) -> Tuple[bool, Optional[int]]:
        if self._redis is None or self._redis_broken:
            return False, None
        try:
            raw = self._redis.get(key)
        except Exception as exc:  # noqa: BLE001
            self._redis_broken = True
            _bump("errors")
            logger.warning("mapbiomas: Redis GET failed (%s); LRU only", exc)
            return False, None
        if raw is None:
            return False, None
        try:
            v = json.loads(raw)
        except (TypeError, ValueError):
            return False, None
        if v is None:
            return True, None
        try:
            return True, int(v)
        except (TypeError, ValueError):
            return False, None

    def _redis_put(self, key: str, value: Optional[int]) -> None:
        if self._redis is None or self._redis_broken:
            return
        try:
            self._redis.set(
                key, json.dumps(value), ex=MAPBIOMAS_REDIS_TTL_S
            )
        except Exception as exc:  # noqa: BLE001
            self._redis_broken = True
            _bump("errors")
            logger.warning("mapbiomas: Redis SET failed (%s); LRU only", exc)

    # ------------------------------------------------------------------
    # Public lookup
    # ------------------------------------------------------------------
    def get_clutter_class(
        self, lat: float, lon: float, year: Optional[int] = None
    ) -> Optional[int]:
        """Return the MapBiomas LULC class code at ``(lat, lon)``.

        Returns ``None`` when:
          * no raster is configured (extractor is a no-op),
          * the point falls outside the raster footprint,
          * the cell value is the raster's ``nodata`` sentinel.

        Coordinate validation is light — out-of-range inputs return
        ``None`` rather than raising; clutter is best-effort metadata.
        """
        if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
            return None

        yr = int(year) if year is not None else self._year
        key = _round_key(lat, lon, yr)

        # Tier 1: in-process LRU (fastest)
        hit, val = self._lru.get(key)
        if hit:
            _bump("hits")
            return val

        # Tier 2: Redis (shared across tasks)
        hit, val = self._redis_get(key)
        if hit:
            _bump("hits")
            self._lru.put(key, val)
            return val

        # Tier 3: raster sample
        ds = self._open()
        if ds is None:
            self._lru.put(key, None)
            return None

        try:
            row, col = ds.index(lon, lat)  # rasterio uses (x=lon, y=lat)
        except Exception:  # noqa: BLE001 – out-of-bounds, transform error, etc.
            self._lru.put(key, None)
            self._redis_put(key, None)
            return None

        # Bounds check before issuing a window read.
        if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
            self._lru.put(key, None)
            self._redis_put(key, None)
            _bump("misses")
            return None

        try:
            from rasterio.windows import Window  # type: ignore

            window = Window(col, row, 1, 1)
            arr = ds.read(1, window=window)
            _bump("raster_reads")
        except ImportError:
            # rasterio not installed (test path with fake dataset).
            try:
                arr = ds.read(1, window=(col, row, 1, 1))
                _bump("raster_reads")
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "mapbiomas: raster read failed at %s,%s (%s)", lat, lon, exc
                )
                self._lru.put(key, None)
                return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("mapbiomas: raster read failed at %s,%s (%s)", lat, lon, exc)
            self._lru.put(key, None)
            return None

        if arr.size == 0:
            self._lru.put(key, None)
            self._redis_put(key, None)
            return None

        raw_val = int(arr.flat[0])
        nodata = ds.nodata
        if nodata is not None and raw_val == int(nodata):
            value: Optional[int] = None
        else:
            value = raw_val

        self._lru.put(key, value)
        self._redis_put(key, value)
        _bump("misses")
        return value

    def close(self) -> None:
        if self._dataset is not None:
            try:
                self._dataset.close()
            except Exception:  # noqa: BLE001
                pass
            self._dataset = None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_singleton: Optional[MapBiomasExtractor] = None
_singleton_lock = threading.Lock()


def get_extractor() -> MapBiomasExtractor:
    """Return the process-wide :class:`MapBiomasExtractor` singleton.

    Thread-safe; idempotent. The first call decides the raster path and
    Redis endpoint from environment variables — subsequent calls reuse
    the same instance.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = MapBiomasExtractor()
        return _singleton


def reset_extractor() -> None:
    """Drop the singleton (testing / config reload)."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None
