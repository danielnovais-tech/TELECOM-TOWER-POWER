# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
coverage_predict.py
===================

Terrain-aware signal-strength prediction for the TELECOM TOWER POWER platform.

The module exposes three layers, fronted by ``predict_signal`` /
``predict_coverage_grid``:

1. **Local ML model** — a ridge regression over engineered features derived
   from SRTM terrain profiles plus link parameters.  Trained on synthetic
   samples generated from the platform's physics (`LinkEngine`) augmented
   with terrain obstruction loss and Gaussian noise that mimics measured
   shadow-fading behaviour.  Optionally re-trained on historical
   ``LinkResult`` records.  Persisted as a NumPy ``.npz`` artefact so the
   API process can hot-load it without spinning up scikit-learn.

2. **Amazon SageMaker adapter** — when ``SAGEMAKER_COVERAGE_ENDPOINT`` is
   set, the same engineered feature vector is POSTed to a SageMaker
   real-time endpoint and the returned ``signal_dbm`` is used.  The model
   hosted there can be anything (XGBoost, neural network) as long as it
   accepts the JSON schema documented below.

3. **Amazon Bedrock interpreter** — once a numeric prediction is
   produced, the result can optionally be passed through
   ``bedrock_service.invoke_model`` to generate a natural-language
   coverage assessment (the ``explain`` flag on the public API).

If neither the local model nor SageMaker is available the module
gracefully falls back to the deterministic free-space + Fresnel
estimate already used by ``/analyze``, so the endpoint never returns
500.

SageMaker payload schema
------------------------
Request (``application/json``)::

    {"instances": [{"features": [<float>, ...]}]}

Response::

    {"predictions": [{"signal_dbm": <float>}]}

Both payloads use the feature order returned by :func:`feature_names`.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_PATH = os.getenv("COVERAGE_MODEL_PATH", "coverage_model.npz")
MODEL_S3_URI = os.getenv("COVERAGE_MODEL_S3_URI", "")  # e.g. s3://bucket/models/coverage_model.npz
# Optional directory of per-band ridge artefacts (coverage_model_<MHz>.npz).
# When set and at least one band model is present we prefer band-specific
# coefficients over the single global model. See ``BandAwareCoverageModel``.
BAND_MODEL_DIR = os.getenv("COVERAGE_BAND_MODEL_DIR", "")
# Optional S3 prefix that mirrors BAND_MODEL_DIR. Files under this prefix
# (coverage_model_<MHz>.npz, coverage_model_global.npz, manifest.json) are
# downloaded into BAND_MODEL_DIR on container start.
BAND_MODELS_S3_PREFIX = os.getenv("COVERAGE_BAND_MODELS_S3_PREFIX", "")
SAGEMAKER_ENDPOINT = os.getenv("SAGEMAKER_COVERAGE_ENDPOINT", "")
SAGEMAKER_REGION = os.getenv("SAGEMAKER_REGION", os.getenv("AWS_REGION", "us-east-1"))

# Nominal commercial cellular bands present in the Brazilian market.
# Band-aware model artefacts are keyed by these integer MHz values.
_NOMINAL_BANDS_MHZ: Tuple[int, ...] = (700, 850, 900, 1800, 2100, 2600, 3500)
# Band used when an incoming frequency does not have a dedicated artefact
# (e.g. 1900 MHz PCS, 28 GHz mmWave). 1800 MHz sits in the middle of the
# coverage-grade spectrum and behaves closest to the median path.
_FALLBACK_BAND_MHZ: int = 1800

# Sentinel signal used when an output cannot be computed.
_FLOOR_DBM = -140.0

# Feature ordering — keep stable; the persisted model and any SageMaker
# endpoint consume features in this exact order.
_FEATURE_NAMES: Tuple[str, ...] = (
    "log_d_km",          # ln(distance + 1e-3)
    "log_f_ghz",         # ln(frequency in GHz)
    "tx_h_m",            # tx antenna height AGL
    "rx_h_m",            # rx antenna height AGL
    "tx_power_dbm",
    "tx_gain_dbi",
    "rx_gain_dbi",
    "terrain_mean_m",
    "terrain_max_m",
    "terrain_std_m",
    "terrain_slope_m_per_km",
    "n_obstructions",
    "max_obstruction_m",
    "min_fresnel_ratio",
    # Engineered nonlinear terms — let a linear model approximate FSPL
    # and shadowing without resorting to a tree ensemble.
    "log_d_km_sq",
    "log_d_km_x_log_f_ghz",
    "terrain_std_x_log_d",
)


def feature_names(*, with_clutter: bool = False) -> Tuple[str, ...]:
    """Public, ordered feature names accepted by the model.

    When ``with_clutter=True``, the 10-dim MapBiomas LULC one-hot is
    appended in canonical order (see :data:`mapbiomas_clutter.ONE_HOT_FEATURE_NAMES`).
    """
    if not with_clutter:
        return _FEATURE_NAMES
    try:
        from mapbiomas_clutter import ONE_HOT_FEATURE_NAMES
    except Exception:
        return _FEATURE_NAMES
    return _FEATURE_NAMES + ONE_HOT_FEATURE_NAMES


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _summarise_terrain(
    terrain_profile: Sequence[float],
    d_km: float,
    f_hz: float,
    tx_h_asl: float,
    rx_h_asl: float,
) -> Dict[str, float]:
    """Reduce a terrain elevation profile into scalar features."""
    if not terrain_profile or len(terrain_profile) < 2:
        return {
            "terrain_mean_m": 0.0,
            "terrain_max_m": 0.0,
            "terrain_std_m": 0.0,
            "terrain_slope_m_per_km": 0.0,
            "n_obstructions": 0.0,
            "max_obstruction_m": 0.0,
            "min_fresnel_ratio": 1.0,
        }
    arr = np.asarray(terrain_profile, dtype=float)
    n = len(arr)
    step = d_km / (n - 1)
    los_line = np.linspace(tx_h_asl, rx_h_asl, n)

    # Earth bulge correction (k = 4/3)
    R_eff_m = 6371.0 * 1.33 * 1000
    idx = np.arange(n)
    d1_m = idx * step * 1000
    d2_m = (d_km - idx * step) * 1000
    earth_bulge = (d1_m * d2_m) / (2 * R_eff_m)

    clearance = los_line - arr - earth_bulge       # metres above ground
    obstructions = clearance < 0
    max_obstruction = float(max(0.0, -clearance.min()))

    # First-Fresnel-zone radius at each point
    c = 299_792_458.0
    safe_d1 = np.clip(d1_m, 1.0, None)
    safe_d2 = np.clip(d2_m, 1.0, None)
    fresnel = np.sqrt((c * safe_d1 * safe_d2) / (f_hz * (safe_d1 + safe_d2)))
    # Avoid div-by-zero at the endpoints
    interior = (idx > 0) & (idx < n - 1)
    if interior.any():
        ratios = clearance[interior] / fresnel[interior]
        min_ratio = float(np.min(ratios))
    else:
        min_ratio = 1.0

    return {
        "terrain_mean_m": float(arr.mean()),
        "terrain_max_m": float(arr.max()),
        "terrain_std_m": float(arr.std()),
        "terrain_slope_m_per_km": float((arr[-1] - arr[0]) / max(d_km, 1e-3)),
        "n_obstructions": float(int(obstructions.sum())),
        "max_obstruction_m": max_obstruction,
        "min_fresnel_ratio": min_ratio,
    }


def build_features(
    *,
    d_km: float,
    f_hz: float,
    tx_h_m: float,
    rx_h_m: float,
    tx_power_dbm: float,
    tx_gain_dbi: float,
    rx_gain_dbi: float,
    terrain_profile: Optional[Sequence[float]] = None,
    tx_ground_elev_m: float = 0.0,
    rx_ground_elev_m: float = 0.0,
    with_clutter: bool = False,
    rx_lat: Optional[float] = None,
    rx_lon: Optional[float] = None,
) -> np.ndarray:
    """Return the engineered feature vector in canonical order.

    When ``with_clutter=True`` the result is extended by a 10-dim
    MapBiomas LULC one-hot at ``(rx_lat, rx_lon)``. Without coords (or
    with no raster configured) the one-hot collapses to the "Other"
    slot — same column dimension either way so a model trained with
    clutter can still score points whose receiver location is unknown.
    """
    tx_h_asl = tx_ground_elev_m + tx_h_m
    rx_h_asl = rx_ground_elev_m + rx_h_m
    summary = _summarise_terrain(
        terrain_profile or [], d_km, f_hz, tx_h_asl, rx_h_asl
    )

    log_d = math.log(max(d_km, 1e-3))
    log_f_ghz = math.log(max(f_hz / 1e9, 1e-3))

    raw = {
        "log_d_km": log_d,
        "log_f_ghz": log_f_ghz,
        "tx_h_m": float(tx_h_m),
        "rx_h_m": float(rx_h_m),
        "tx_power_dbm": float(tx_power_dbm),
        "tx_gain_dbi": float(tx_gain_dbi),
        "rx_gain_dbi": float(rx_gain_dbi),
        "log_d_km_sq": log_d * log_d,
        "log_d_km_x_log_f_ghz": log_d * log_f_ghz,
        "terrain_std_x_log_d": summary["terrain_std_m"] * log_d,
    }
    raw.update(summary)
    base = np.array([raw[k] for k in _FEATURE_NAMES], dtype=float)
    if not with_clutter:
        return base

    # Append clutter one-hot. Failures (no rasterio, no raster, lookup
    # error) collapse to the "Other" slot — same dimension, never raises.
    try:
        from mapbiomas_clutter import (
            clutter_class_to_onehot,
            get_extractor,
        )
        code: Optional[int] = None
        if rx_lat is not None and rx_lon is not None:
            try:
                code = get_extractor().get_clutter_class(rx_lat, rx_lon)
            except Exception:  # noqa: BLE001
                code = None
        onehot = clutter_class_to_onehot(code)
    except Exception:  # noqa: BLE001
        # mapbiomas_clutter import itself failed — emit zero vector of
        # the expected length so feature dimension stays consistent.
        onehot = np.zeros(10, dtype=float)
    return np.concatenate([base, onehot])


# ---------------------------------------------------------------------------
# Local model (ridge regression in NumPy)
# ---------------------------------------------------------------------------

@dataclass
class CoverageModel:
    weights: np.ndarray                    # shape (F + 1,)  – includes intercept
    feature_mean: np.ndarray               # shape (F,)
    feature_std: np.ndarray                # shape (F,)
    version: str = "ridge-v1"
    trained_at: float = field(default_factory=time.time)
    rmse_db: float = 0.0                   # in-sample (training) RMSE
    n_train: int = 0
    # ── Calibration metrics (added 2026-05) ────────────────────────────
    cv_rmse_db: float = 0.0                # mean k-fold holdout RMSE
    cv_rmse_std_db: float = 0.0            # stddev across folds
    cv_folds: int = 0                      # k value used (0 = not evaluated)
    rmse_by_morphology: Dict[str, float] = field(default_factory=dict)
    rmse_by_band: Dict[str, float] = field(default_factory=dict)
    # Feature schema baked into this artefact. Defaults to the v1 list
    # for backward compatibility with older .npz files that didn't
    # serialise this field. v2 = v1 + 10-dim MapBiomas one-hot.
    feature_names: Tuple[str, ...] = field(
        default_factory=lambda: tuple(_FEATURE_NAMES)
    )

    # ---- prediction --------------------------------------------------------

    def predict(self, features: np.ndarray) -> float:
        x = (features - self.feature_mean) / np.where(self.feature_std == 0, 1.0, self.feature_std)
        x = np.append(x, 1.0)  # intercept term
        return float(np.dot(self.weights, x))

    # ---- persistence -------------------------------------------------------

    def save(self, path: str) -> None:
        np.savez(
            path,
            weights=self.weights,
            feature_mean=self.feature_mean,
            feature_std=self.feature_std,
            meta=np.frombuffer(
                json.dumps({
                    "version": self.version,
                    "trained_at": self.trained_at,
                    "rmse_db": self.rmse_db,
                    "n_train": self.n_train,
                    "cv_rmse_db": self.cv_rmse_db,
                    "cv_rmse_std_db": self.cv_rmse_std_db,
                    "cv_folds": self.cv_folds,
                    "rmse_by_morphology": self.rmse_by_morphology,
                    "rmse_by_band": self.rmse_by_band,
                    "feature_names": list(self.feature_names),
                }).encode("utf-8"),
                dtype=np.uint8,
            ),
        )
        logger.info("Saved coverage model to %s (rmse=%.2f dB, cv=%.2f±%.2f dB, n=%d)",
                    path, self.rmse_db, self.cv_rmse_db, self.cv_rmse_std_db,
                    self.n_train)

    @classmethod
    def load(cls, path: str) -> "CoverageModel":
        # SECURITY: allow_pickle=False (default) prevents arbitrary code
        # execution if a model file is tampered with. Meta is stored as
        # a uint8 byte buffer of JSON, never as a pickled object.
        with np.load(path, allow_pickle=False) as npz:
            meta_bytes = bytes(np.asarray(npz["meta"], dtype=np.uint8).tobytes())
            meta = json.loads(meta_bytes.decode("utf-8"))
            return cls(
                weights=npz["weights"],
                feature_mean=npz["feature_mean"],
                feature_std=npz["feature_std"],
                version=meta.get("version", "ridge-v1"),
                trained_at=meta.get("trained_at", 0.0),
                rmse_db=meta.get("rmse_db", 0.0),
                n_train=meta.get("n_train", 0),
                cv_rmse_db=meta.get("cv_rmse_db", 0.0),
                cv_rmse_std_db=meta.get("cv_rmse_std_db", 0.0),
                cv_folds=meta.get("cv_folds", 0),
                rmse_by_morphology=dict(meta.get("rmse_by_morphology", {}) or {}),
                rmse_by_band=dict(meta.get("rmse_by_band", {}) or {}),
                feature_names=tuple(
                    meta.get("feature_names", list(_FEATURE_NAMES)) or _FEATURE_NAMES
                ),
            )


_model_cache: Optional[CoverageModel] = None
_model_loaded_at: float = 0.0


# ---------------------------------------------------------------------------
# Band-aware model (one ridge per nominal commercial band)
# ---------------------------------------------------------------------------

@dataclass
class BandAwareCoverageModel:
    """Collection of per-band ridge regressions.

    Path-loss exponent, intercept, and the slope of every covariate vary
    measurably with carrier frequency: 700 MHz penetrates buildings and
    suffers less foliage loss than 3.5 GHz on the same path. Fitting a
    single global model averages those regimes and underestimates
    confidence intervals at the band extremes.

    This wrapper holds one :class:`CoverageModel` per nominal MHz band
    (700 / 850 / 900 / 1800 / 2100 / 2600 / 3500) plus an optional
    *global* model used when no per-band artefact is available for the
    requested frequency. At prediction time we snap the requested
    ``f_hz`` to the closest nominal band, fall back to ``fallback_band``
    (1800 MHz) if that artefact is missing, then to the global model,
    and finally back to the physics estimate via the caller.

    Persistence layout::

        <dir>/coverage_model_700.npz
        <dir>/coverage_model_850.npz
        ...
        <dir>/coverage_model_3500.npz
        <dir>/coverage_model_global.npz   (optional)
        <dir>/manifest.json               (band metadata index)
    """

    models: Dict[int, CoverageModel] = field(default_factory=dict)
    global_model: Optional[CoverageModel] = None
    fallback_band: int = _FALLBACK_BAND_MHZ
    trained_at: float = field(default_factory=time.time)

    # ---- prediction ----------------------------------------------------

    def pick(self, f_hz: float) -> Tuple[Optional[CoverageModel], int]:
        """Return ``(model, band_mhz)`` for the given frequency.

        Resolution order:
          1. Exact-nearest nominal band (e.g. 1.95 GHz → 2100 MHz).
          2. Configured fallback band (1800 MHz by default).
          3. The global single-band model, if present.
          4. ``(None, 0)`` — caller must use the physics fallback.
        """
        nearest = _nearest_band_mhz(f_hz)
        m = self.models.get(nearest)
        if m is not None:
            return m, nearest
        if self.fallback_band in self.models:
            return self.models[self.fallback_band], self.fallback_band
        if self.global_model is not None:
            return self.global_model, 0
        return None, 0

    def predict(self, features: np.ndarray, *, f_hz: Optional[float] = None) -> Tuple[float, int]:
        """Predict ``(signal_dbm, band_mhz_used)``.

        ``f_hz`` is optional: when omitted we recover it from the
        ``log_f_ghz`` slot of the feature vector — that is the
        canonical location in :data:`_FEATURE_NAMES` and avoids a
        second source of truth.
        """
        if f_hz is None:
            f_hz = math.exp(float(features[_FEATURE_NAMES.index("log_f_ghz")])) * 1e9
        m, band = self.pick(f_hz)
        if m is None:
            raise RuntimeError("no band model and no global fallback available")
        return m.predict(features), band

    # ---- persistence ---------------------------------------------------

    def save_dir(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        manifest: Dict[str, Any] = {
            "trained_at": self.trained_at,
            "fallback_band": self.fallback_band,
            "bands": {},
        }
        for band, m in sorted(self.models.items()):
            path = os.path.join(directory, f"coverage_model_{band}.npz")
            m.save(path)
            manifest["bands"][str(band)] = {
                "rmse_db": m.rmse_db,
                "n_train": m.n_train,
                "cv_rmse_db": m.cv_rmse_db,
                "cv_folds": m.cv_folds,
            }
        if self.global_model is not None:
            self.global_model.save(os.path.join(directory, "coverage_model_global.npz"))
            manifest["global"] = {
                "rmse_db": self.global_model.rmse_db,
                "n_train": self.global_model.n_train,
            }
        with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        logger.info("Saved band-aware model to %s (%d bands)", directory, len(self.models))

    @classmethod
    def load_dir(cls, directory: str) -> "BandAwareCoverageModel":
        models: Dict[int, CoverageModel] = {}
        for band in _NOMINAL_BANDS_MHZ:
            path = os.path.join(directory, f"coverage_model_{band}.npz")
            if os.path.exists(path):
                try:
                    models[band] = CoverageModel.load(path)
                except Exception:
                    logger.exception("Failed to load band model %s", path)
        global_path = os.path.join(directory, "coverage_model_global.npz")
        global_model: Optional[CoverageModel] = None
        if os.path.exists(global_path):
            try:
                global_model = CoverageModel.load(global_path)
            except Exception:
                logger.exception("Failed to load global model %s", global_path)
        if not models and global_model is None:
            raise FileNotFoundError(f"no band artefacts under {directory!r}")
        return cls(models=models, global_model=global_model)

    # ---- introspection -------------------------------------------------

    def info(self) -> Dict[str, Any]:
        """Serializable summary suitable for ``/coverage/model/info``."""
        out: Dict[str, Any] = {
            "kind": "band-aware",
            "fallback_band": self.fallback_band,
            "bands": {},
        }
        for band, m in sorted(self.models.items()):
            out["bands"][band] = {
                "rmse_db": round(m.rmse_db, 4),
                "cv_rmse_db": round(m.cv_rmse_db, 4),
                "cv_folds": m.cv_folds,
                "n_train": m.n_train,
                "version": m.version,
            }
        if self.global_model is not None:
            out["global"] = {
                "rmse_db": round(self.global_model.rmse_db, 4),
                "cv_rmse_db": round(self.global_model.cv_rmse_db, 4),
                "n_train": self.global_model.n_train,
            }
        return out


_band_model_cache: Optional[BandAwareCoverageModel] = None
_band_model_loaded_at: float = 0.0


def get_band_model(refresh: bool = False) -> Optional[BandAwareCoverageModel]:
    """Return the cached band-aware model, lazily loading from
    ``COVERAGE_BAND_MODEL_DIR``. Returns ``None`` when not configured or
    when no artefacts are present.
    """
    global _band_model_cache, _band_model_loaded_at
    if not BAND_MODEL_DIR:
        return None
    if _band_model_cache is not None and not refresh:
        return _band_model_cache
    if not os.path.isdir(BAND_MODEL_DIR):
        return None
    try:
        _band_model_cache = BandAwareCoverageModel.load_dir(BAND_MODEL_DIR)
        _band_model_loaded_at = time.time()
        logger.info(
            "Loaded band-aware coverage model from %s (%d bands)",
            BAND_MODEL_DIR, len(_band_model_cache.models),
        )
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to load band-aware model from %s", BAND_MODEL_DIR)
        return None
    return _band_model_cache


def _download_from_s3(s3_uri: str, dest: str) -> bool:
    """Download ``s3://bucket/key`` to ``dest``. Returns True on success."""
    if not s3_uri.startswith("s3://"):
        logger.warning("COVERAGE_MODEL_S3_URI must start with s3:// (got %r)", s3_uri)
        return False
    try:
        import boto3
        bucket, _, key = s3_uri[len("s3://"):].partition("/")
        if not bucket or not key:
            logger.warning("Malformed COVERAGE_MODEL_S3_URI: %r", s3_uri)
            return False
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        boto3.client("s3").download_file(bucket, key, dest)
        logger.info("Downloaded coverage model from %s to %s", s3_uri, dest)
        return True
    except Exception:
        logger.exception("Failed to download coverage model from %s", s3_uri)
        return False


def refresh_from_s3() -> bool:
    """Force-download the model from ``COVERAGE_MODEL_S3_URI`` to ``MODEL_PATH``.

    Called from the container entrypoint so each boot picks up the latest
    artefact published by the nightly retrain workflow without rebuilding the
    image. Returns True on success (or when no S3 URI is configured — the
    baked-in artefact is fine), False on download failure.
    """
    if not MODEL_S3_URI:
        return True
    return _download_from_s3(MODEL_S3_URI, MODEL_PATH)


def refresh_band_models_from_s3() -> bool:
    """Sync ``s3://<prefix>/coverage_model_*.npz`` into ``BAND_MODEL_DIR``.

    Returns True on success (or when no prefix is configured — same
    semantics as :func:`refresh_from_s3`). Failures are logged and
    return False so the entrypoint can decide whether to abort.
    """
    if not BAND_MODELS_S3_PREFIX or not BAND_MODEL_DIR:
        return True
    if not BAND_MODELS_S3_PREFIX.startswith("s3://"):
        logger.warning(
            "COVERAGE_BAND_MODELS_S3_PREFIX must start with s3:// (got %r)",
            BAND_MODELS_S3_PREFIX,
        )
        return False
    try:
        import boto3
        bucket, _, prefix = BAND_MODELS_S3_PREFIX[len("s3://"):].partition("/")
        if not bucket:
            logger.warning("Malformed COVERAGE_BAND_MODELS_S3_PREFIX: %r",
                           BAND_MODELS_S3_PREFIX)
            return False
        prefix = prefix.rstrip("/")
        os.makedirs(BAND_MODEL_DIR, exist_ok=True)
        s3 = boto3.client("s3")
        # Whitelist of keys we expect to find under the prefix; resists a
        # tampered bucket from injecting arbitrary files into BAND_MODEL_DIR.
        wanted = (
            [f"coverage_model_{b}.npz" for b in _NOMINAL_BANDS_MHZ]
            + ["coverage_model_global.npz", "manifest.json"]
        )
        downloaded = 0
        for name in wanted:
            key = f"{prefix}/{name}" if prefix else name
            dest = os.path.join(BAND_MODEL_DIR, name)
            try:
                s3.download_file(bucket, key, dest)
                downloaded += 1
            except Exception:
                # Missing keys are common (e.g. no global fallback) — not
                # an error. Log at debug, not warning.
                logger.debug("band artefact %s not present in S3", key)
        logger.info(
            "Synced %d band artefacts from %s to %s",
            downloaded, BAND_MODELS_S3_PREFIX, BAND_MODEL_DIR,
        )
        # Bust the cache so the next get_band_model() call sees fresh files.
        global _band_model_cache
        _band_model_cache = None
        return downloaded > 0
    except Exception:
        logger.exception("Failed to sync band models from %s", BAND_MODELS_S3_PREFIX)
        return False


def get_model(refresh: bool = False) -> Optional[CoverageModel]:
    """Return the cached local model, lazily loading it from disk.

    If the artefact is missing locally and ``COVERAGE_MODEL_S3_URI`` is set,
    transparently download it from S3 once on first access.
    """
    global _model_cache, _model_loaded_at
    if _model_cache is not None and not refresh:
        return _model_cache
    if not os.path.exists(MODEL_PATH):
        if MODEL_S3_URI and _download_from_s3(MODEL_S3_URI, MODEL_PATH):
            pass  # fall through to load
        else:
            return None
    try:
        _model_cache = CoverageModel.load(MODEL_PATH)
        _model_loaded_at = time.time()
        logger.info("Loaded coverage model %s (rmse=%.2f dB)",
                    _model_cache.version, _model_cache.rmse_db)
    except Exception:
        logger.exception("Failed to load coverage model from %s", MODEL_PATH)
        return None
    return _model_cache


# ---------------------------------------------------------------------------
# Training (synthetic + optional historical)
# ---------------------------------------------------------------------------

def _physics_signal(
    *, d_km: float, f_hz: float, tx_power_dbm: float, tx_gain: float,
    rx_gain: float, terrain_profile: Sequence[float], tx_h_m: float, rx_h_m: float,
) -> float:
    """Ground-truth label generator for synthetic samples.

    Mirrors the platform's physics: free-space path loss + a Fresnel
    obstruction penalty, plus log-normal shadow fading (sigma=6 dB) so
    the model has to learn from terrain features, not memorise.
    """
    d_m = max(d_km, 1e-3) * 1000
    fspl = 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    # Quick Fresnel obstruction proxy — same idea as LinkEngine.terrain_clearance
    n = len(terrain_profile)
    if n >= 2:
        tx_asl = terrain_profile[0] + tx_h_m
        rx_asl = terrain_profile[-1] + rx_h_m
        c = 299_792_458.0
        min_ratio = float("inf")
        for i in range(1, n - 1):
            d1 = d_km * i / (n - 1)
            d2 = d_km - d1
            line_h = tx_asl + (rx_asl - tx_asl) * (d1 / d_km)
            r = math.sqrt(c * d1 * d2 * 1e6 / (f_hz * (d1 + d2) * 1000))
            min_ratio = min(min_ratio, (line_h - terrain_profile[i]) / max(r, 1e-3))
        if min_ratio < 0.6:
            fspl += (0.6 - min_ratio) * 12.0   # diffraction penalty in dB

    shadow = random.gauss(0.0, 6.0)
    return tx_power_dbm + tx_gain + rx_gain - fspl + shadow


def _synth_terrain_profile(
    d_km: float, base_elev: float, roughness: float, n_pts: int = 16,
) -> List[float]:
    """Random but plausible elevation profile (1D fractal-ish)."""
    profile = [base_elev]
    for _ in range(n_pts - 1):
        profile.append(max(0.0, profile[-1] + random.gauss(0.0, roughness)))
    return profile


def _generate_synthetic_dataset(n: int) -> Tuple[np.ndarray, np.ndarray]:
    bands = [700e6, 850e6, 900e6, 1.8e9, 2.1e9, 2.6e9, 3.5e9]
    X, y = [], []
    for _ in range(n):
        d_km = max(0.05, random.lognormvariate(0.5, 1.0))   # ~0.05–60 km
        f_hz = random.choice(bands)
        tx_h = random.uniform(15, 80)
        rx_h = random.uniform(2, 40)
        tx_p = random.uniform(30, 47)
        tx_g = random.uniform(10, 18)
        rx_g = random.uniform(2, 18)
        base = random.uniform(0, 1500)
        roughness = random.uniform(0.5, 25)
        profile = _synth_terrain_profile(d_km, base, roughness)
        feats = build_features(
            d_km=d_km, f_hz=f_hz, tx_h_m=tx_h, rx_h_m=rx_h,
            tx_power_dbm=tx_p, tx_gain_dbi=tx_g, rx_gain_dbi=rx_g,
            terrain_profile=profile,
            tx_ground_elev_m=profile[0], rx_ground_elev_m=profile[-1],
        )
        label = _physics_signal(
            d_km=d_km, f_hz=f_hz, tx_power_dbm=tx_p, tx_gain=tx_g,
            rx_gain=rx_g, terrain_profile=profile, tx_h_m=tx_h, rx_h_m=rx_h,
        )
        X.append(feats)
        y.append(label)
    return np.asarray(X), np.asarray(y)


def load_historical_from_stores(
    *,
    include_observations: bool = True,
    include_opencellid: bool = False,  # deprecated, retained for arg compat
    max_observations: Optional[int] = None,
    max_opencellid: Optional[int] = None,  # deprecated, ignored
) -> List[Tuple[np.ndarray, float]]:
    """Build training tuples from the persisted label store.

    Only ``link_observations`` rows are used — real point-to-point
    measurements where the receiver position, antenna params, and
    ``observed_dbm`` are all known. No terrain profile is fetched here
    (avoid round-trips at train time); ``build_features`` falls back to
    zero terrain features when none is provided. The local model already
    includes ``log_d_km`` and frequency terms, so it can still learn a
    useful correction.

    The ``cell_signal_samples`` table (OpenCelliD ``averageSignal``
    aggregates) is no longer consulted: the free tier returns 0 for
    100 %% of Brazilian rows (verified empirically 2026-04-30 with token
    pk.e560… → 54 549 rows downloaded, 0 with non-zero averageSignal).
    The ``include_opencellid`` and ``max_opencellid`` kwargs are kept
    for argument compatibility but ignored.
    """
    del include_opencellid, max_opencellid  # silence unused warnings
    from observation_store import ObservationStore  # local import to avoid cycles
    store = ObservationStore()
    out: List[Tuple[np.ndarray, float]] = []

    if include_observations:
        for i, row in enumerate(store.iter_observations()):
            if max_observations is not None and i >= max_observations:
                break
            d_km = haversine_km(
                row["tx_lat"], row["tx_lon"], row["rx_lat"], row["rx_lon"],
            )
            feats = build_features(
                d_km=max(d_km, 1e-3),
                f_hz=float(row["freq_hz"]),
                tx_h_m=float(row["tx_height_m"]),
                rx_h_m=float(row["rx_height_m"]),
                tx_power_dbm=float(row["tx_power_dbm"]),
                tx_gain_dbi=float(row["tx_gain_dbi"]),
                rx_gain_dbi=float(row["rx_gain_dbi"]),
                terrain_profile=None,
            )
            out.append((feats, float(row["observed_dbm"])))

    return out


def train_model(
    n_synthetic: int = 5000,
    historical: Optional[Sequence[Tuple[np.ndarray, float]]] = None,
    l2: float = 1.0,
    seed: int = 42,
    save_to: Optional[str] = None,
    kfold: int = 5,
) -> CoverageModel:
    """Train the ridge regression model and (optionally) persist it.

    With ``kfold >= 2`` (default 5) the function also performs k-fold
    cross-validation **on the synthetic dataset only** (real
    ``link_observations`` rows are too few + too valuable to hold out
    in early-stage training) and reports holdout RMSE plus per-band /
    per-morphology breakdown. Set ``kfold=0`` to skip CV (useful for
    unit tests where determinism + speed matter).
    """
    random.seed(seed)
    np.random.seed(seed)

    X_syn, y_syn = _generate_synthetic_dataset(n_synthetic)
    if historical:
        X_hist = np.vstack([row[0] for row in historical])
        y_hist = np.asarray([row[1] for row in historical])
        # Up-weight real measurements 3x
        X = np.vstack([X_syn, X_hist, X_hist, X_hist])
        y = np.concatenate([y_syn, y_hist, y_hist, y_hist])
    else:
        X, y = X_syn, y_syn
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std_safe = np.where(std == 0, 1.0, std)
    Xn = (X - mean) / std_safe
    Xn_b = np.hstack([Xn, np.ones((len(Xn), 1))])

    # Closed-form ridge: w = (XᵀX + λI)⁻¹ Xᵀy.  Don't penalise intercept.
    F = Xn_b.shape[1]
    reg = l2 * np.eye(F)
    reg[-1, -1] = 0.0
    w = np.linalg.solve(Xn_b.T @ Xn_b + reg, Xn_b.T @ y)

    preds = Xn_b @ w
    rmse = float(np.sqrt(np.mean((preds - y) ** 2)))

    # ── K-fold cross-validation on synthetic data ───────────────────────
    cv_rmse = 0.0
    cv_std = 0.0
    cv_used = 0
    rmse_by_morph: Dict[str, float] = {}
    rmse_by_band: Dict[str, float] = {}
    if kfold and kfold >= 2 and len(X_syn) >= kfold * 10:
        cv_used = int(kfold)
        cv_rmse, cv_std, rmse_by_morph, rmse_by_band = _kfold_evaluate(
            X_syn, y_syn, l2=l2, k=cv_used, seed=seed,
        )
        logger.info(
            "K-fold CV (k=%d): test RMSE = %.2f ± %.2f dB", cv_used, cv_rmse, cv_std,
        )

    model = CoverageModel(
        weights=w,
        feature_mean=mean,
        feature_std=std_safe,
        rmse_db=rmse,
        n_train=len(X),
        cv_rmse_db=float(cv_rmse),
        cv_rmse_std_db=float(cv_std),
        cv_folds=cv_used,
        rmse_by_morphology=rmse_by_morph,
        rmse_by_band=rmse_by_band,
    )
    if save_to:
        model.save(save_to)
        # Force reload on next call
        global _model_cache
        _model_cache = model
    return model


# ---------------------------------------------------------------------------
# Band-aware training
# ---------------------------------------------------------------------------

# Minimum samples required to fit a useful per-band ridge (16 features
# + intercept = 17 parameters). Below this we skip the band rather than
# overfit; ``predict_signal`` will fall back to the 1800 MHz or global
# model for that frequency.
_MIN_SAMPLES_PER_BAND = 60


def _split_by_nominal_band(
    X: np.ndarray, y: np.ndarray,
) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
    """Group samples by closest nominal band, using the ``log_f_ghz``
    feature column as the source of truth.
    """
    groups: Dict[int, List[int]] = {}
    for i in range(len(X)):
        f_hz = math.exp(float(X[i, _LOG_F_IDX])) * 1e9
        band = _nearest_band_mhz(f_hz)
        groups.setdefault(band, []).append(i)
    return {
        band: (X[np.asarray(ids)], y[np.asarray(ids)])
        for band, ids in groups.items()
    }


def train_band_aware_model(
    n_synthetic: int = 8000,
    historical: Optional[Sequence[Tuple[np.ndarray, float]]] = None,
    l2: float = 1.0,
    seed: int = 42,
    save_to_dir: Optional[str] = None,
    kfold: int = 5,
    train_global_fallback: bool = True,
) -> BandAwareCoverageModel:
    """Train one ridge per nominal commercial band.

    The synthetic generator already emits each of the seven Brazilian
    bands uniformly, so an 8 000-sample run yields ~1 100 samples per
    band — well above ``_MIN_SAMPLES_PER_BAND``. Real
    ``link_observations`` rows are routed to whichever band their
    frequency snaps closest to and up-weighted 3× (mirrors
    :func:`train_model`).

    When ``train_global_fallback`` is true a single all-data ridge is
    also fit and persisted as ``coverage_model_global.npz`` so requests
    on rare bands (e.g. 28 GHz mmWave, 450 MHz iDEN) still get an ML
    answer instead of dropping to the physics fallback.
    """
    random.seed(seed)
    np.random.seed(seed)

    X_syn, y_syn = _generate_synthetic_dataset(n_synthetic)
    if historical:
        X_hist = np.vstack([row[0] for row in historical])
        y_hist = np.asarray([row[1] for row in historical])
        X_full = np.vstack([X_syn, X_hist, X_hist, X_hist])
        y_full = np.concatenate([y_syn, y_hist, y_hist, y_hist])
    else:
        X_full, y_full = X_syn, y_syn

    band_data = _split_by_nominal_band(X_full, y_full)
    band_models: Dict[int, CoverageModel] = {}
    for band in _NOMINAL_BANDS_MHZ:
        if band not in band_data:
            continue
        Xb, yb = band_data[band]
        if len(Xb) < _MIN_SAMPLES_PER_BAND:
            logger.warning(
                "Skipping band %d MHz: only %d samples (< %d).",
                band, len(Xb), _MIN_SAMPLES_PER_BAND,
            )
            continue

        w, mean, std_safe = _fit_ridge(Xb, yb, l2=l2)
        Xn_b = np.hstack([
            (Xb - mean) / std_safe, np.ones((len(Xb), 1)),
        ])
        rmse = float(np.sqrt(np.mean((Xn_b @ w - yb) ** 2)))

        cv_rmse = 0.0
        cv_std = 0.0
        cv_used = 0
        if kfold and kfold >= 2 and len(Xb) >= kfold * 10:
            cv_used = int(kfold)
            cv_rmse, cv_std, _, _ = _kfold_evaluate(
                Xb, yb, l2=l2, k=cv_used, seed=seed,
            )

        band_models[band] = CoverageModel(
            weights=w,
            feature_mean=mean,
            feature_std=std_safe,
            version=f"ridge-band-{band}-v1",
            rmse_db=rmse,
            n_train=len(Xb),
            cv_rmse_db=float(cv_rmse),
            cv_rmse_std_db=float(cv_std),
            cv_folds=cv_used,
        )
        logger.info(
            "Trained %d MHz band: rmse=%.2f dB, cv=%.2f±%.2f dB, n=%d",
            band, rmse, cv_rmse, cv_std, len(Xb),
        )

    global_model: Optional[CoverageModel] = None
    if train_global_fallback:
        # Re-fit ridge on the full pooled dataset (avoids the dataset
        # rebuild that calling ``train_model`` again would force).
        w, mean, std_safe = _fit_ridge(X_full, y_full, l2=l2)
        Xn_b = np.hstack([
            (X_full - mean) / std_safe, np.ones((len(X_full), 1)),
        ])
        rmse = float(np.sqrt(np.mean((Xn_b @ w - y_full) ** 2)))
        global_model = CoverageModel(
            weights=w,
            feature_mean=mean,
            feature_std=std_safe,
            version="ridge-global-v1",
            rmse_db=rmse,
            n_train=len(X_full),
        )

    band_aware = BandAwareCoverageModel(
        models=band_models,
        global_model=global_model,
        fallback_band=_FALLBACK_BAND_MHZ,
    )

    if save_to_dir:
        band_aware.save_dir(save_to_dir)
        global _band_model_cache
        _band_model_cache = band_aware

    return band_aware


# ---------------------------------------------------------------------------
# Cross-validation helpers
# ---------------------------------------------------------------------------

# Morphology buckets are derived from terrain_std_m (column index 9 in
# _FEATURE_NAMES). Thresholds chosen to align with how field engineers
# describe Brazilian terrain — see notes/tier1-roadmap.md.
_TERRAIN_STD_IDX = _FEATURE_NAMES.index("terrain_std_m")
_LOG_F_IDX = _FEATURE_NAMES.index("log_f_ghz")


def _morphology_label(terrain_std_m: float) -> str:
    if terrain_std_m < 5.0:
        return "open_or_flat"
    if terrain_std_m < 15.0:
        return "rural_rolling"
    return "rural_mountainous"


def _band_label(log_f_ghz: float) -> str:
    f_ghz = math.exp(log_f_ghz)
    # Bucket to nominal commercial band names (closest match).
    bands = [
        (0.7, "700MHz"), (0.85, "850MHz"), (0.9, "900MHz"),
        (1.8, "1800MHz"), (2.1, "2100MHz"), (2.6, "2600MHz"),
        (3.5, "3500MHz"),
    ]
    return min(bands, key=lambda b: abs(b[0] - f_ghz))[1]


def _nearest_band_mhz(f_hz: float) -> int:
    """Map an arbitrary frequency in Hz to the closest nominal band in MHz.

    Used by :class:`BandAwareCoverageModel` to pick the correct per-band
    ridge artefact at prediction time. The mapping is closest-by-MHz, so
    e.g. 2.3 GHz → 2100, 1.9 GHz → 1800, 4.0 GHz → 3500.
    """
    f_mhz = f_hz / 1e6
    return min(_NOMINAL_BANDS_MHZ, key=lambda b: abs(b - f_mhz))


def _fit_ridge(X: np.ndarray, y: np.ndarray, l2: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (weights, feature_mean, feature_std_safe)."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std_safe = np.where(std == 0, 1.0, std)
    Xn = (X - mean) / std_safe
    Xn_b = np.hstack([Xn, np.ones((len(Xn), 1))])
    F = Xn_b.shape[1]
    reg = l2 * np.eye(F)
    reg[-1, -1] = 0.0
    w = np.linalg.solve(Xn_b.T @ Xn_b + reg, Xn_b.T @ y)
    return w, mean, std_safe


def _kfold_evaluate(
    X: np.ndarray, y: np.ndarray, *, l2: float, k: int, seed: int = 42,
) -> Tuple[float, float, Dict[str, float], Dict[str, float]]:
    """K-fold CV. Returns (mean_rmse, std_rmse, rmse_by_morph, rmse_by_band).

    Per-morphology / per-band RMSE is computed on the **concatenated
    holdout predictions** across all folds, so each row gets exactly one
    out-of-fold prediction.
    """
    rng = np.random.RandomState(seed)
    n = len(X)
    idx = rng.permutation(n)
    fold_size = n // k

    fold_rmses: List[float] = []
    holdout_pred = np.zeros(n)
    holdout_y = np.zeros(n)

    for f in range(k):
        start = f * fold_size
        end = (f + 1) * fold_size if f < k - 1 else n
        test_idx = idx[start:end]
        train_idx = np.concatenate([idx[:start], idx[end:]])

        w, m, s = _fit_ridge(X[train_idx], y[train_idx], l2=l2)
        Xtest_n = (X[test_idx] - m) / s
        Xtest_b = np.hstack([Xtest_n, np.ones((len(Xtest_n), 1))])
        preds = Xtest_b @ w

        fold_rmses.append(float(np.sqrt(np.mean((preds - y[test_idx]) ** 2))))
        holdout_pred[test_idx] = preds
        holdout_y[test_idx] = y[test_idx]

    cv_mean = float(np.mean(fold_rmses))
    cv_std = float(np.std(fold_rmses))

    # Per-morphology RMSE on out-of-fold predictions
    morph_groups: Dict[str, List[int]] = {}
    band_groups: Dict[str, List[int]] = {}
    for i in range(n):
        morph_groups.setdefault(_morphology_label(X[i, _TERRAIN_STD_IDX]), []).append(i)
        band_groups.setdefault(_band_label(X[i, _LOG_F_IDX]), []).append(i)

    def _grouped_rmse(groups: Dict[str, List[int]]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for label, ids in groups.items():
            if len(ids) < 5:
                continue  # too few points to be meaningful
            ids_arr = np.asarray(ids)
            err = holdout_pred[ids_arr] - holdout_y[ids_arr]
            out[label] = round(float(np.sqrt(np.mean(err ** 2))), 4)
        return out

    return cv_mean, cv_std, _grouped_rmse(morph_groups), _grouped_rmse(band_groups)


# ---------------------------------------------------------------------------
# SageMaker / Bedrock adapters
# ---------------------------------------------------------------------------

_sagemaker_runtime = None


def _sagemaker_client():
    global _sagemaker_runtime
    if _sagemaker_runtime is None:
        import boto3
        _sagemaker_runtime = boto3.client(
            "sagemaker-runtime", region_name=SAGEMAKER_REGION
        )
    return _sagemaker_runtime


def _predict_sagemaker(features: np.ndarray) -> Optional[float]:
    """Invoke a SageMaker real-time endpoint.  Returns None on any error."""
    if not SAGEMAKER_ENDPOINT:
        return None
    try:
        client = _sagemaker_client()
        body = json.dumps({"instances": [{"features": features.tolist()}]})
        resp = client.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType="application/json",
            Accept="application/json",
            Body=body.encode("utf-8"),
        )
        payload = json.loads(resp["Body"].read())
        preds = payload.get("predictions") or []
        if not preds:
            return None
        first = preds[0]
        if isinstance(first, dict):
            return float(first.get("signal_dbm", first.get("score", 0.0)))
        return float(first)
    except Exception:
        logger.exception("SageMaker invoke_endpoint failed; falling back")
        return None


def explain_with_bedrock(prediction: Dict[str, Any]) -> Optional[str]:
    """Optional natural-language interpretation via Bedrock.

    Returns ``None`` if Bedrock is not configured / errors out.
    """
    try:
        import bedrock_service  # local module
    except Exception:
        return None
    prompt = (
        "Interpret the following coverage prediction for a non-RF stakeholder. "
        "Mention service quality (voice, narrowband IoT, broadband), key "
        "limiting factors, and one concrete remediation. Keep it under 120 words."
    )
    try:
        out = bedrock_service.invoke_model(
            prompt=prompt, context=json.dumps(prediction)
        )
        return out.get("response")
    except Exception:
        logger.exception("Bedrock explanation failed")
        return None


def explain_locally(prediction: Dict[str, Any]) -> str:
    """Deterministic, template-based explanation derived from features.

    Used as a fallback when Bedrock is unavailable. Output is intentionally
    concise (<120 words) and free of hallucination: every claim is anchored to
    a numeric feature in ``prediction``.
    """
    sig = float(prediction.get("signal_dbm", -120.0))
    feasible = bool(prediction.get("feasible", False))
    dist_km = float(prediction.get("distance_km", 0.0))
    feats = prediction.get("features") or {}
    f_ghz = math.exp(float(feats.get("log_f_ghz", 0.0)))
    min_fres = float(feats.get("min_fresnel_ratio", 1.0))
    n_obs = int(feats.get("n_obstructions", 0))
    max_obs = float(feats.get("max_obstruction_m", 0.0))
    slope = float(feats.get("terrain_slope_m_per_km", 0.0))
    tx_h = float(feats.get("tx_h_m", 0.0))

    # Service tier from RSSI bands (typical cellular thresholds)
    if sig >= -75:
        tier = "broadband (HD voice + high-throughput data)"
    elif sig >= -85:
        tier = "broadband (standard data + voice)"
    elif sig >= -95:
        tier = "voice and narrowband IoT only"
    elif sig >= -105:
        tier = "narrowband IoT (NB-IoT/LTE-M) at the edge of usability"
    else:
        tier = "no usable service"

    # Identify the dominant limiting factor
    limits: list[str] = []
    if min_fres < 0.6:
        limits.append(
            f"Fresnel zone obstructed ({min_fres:.0%} of the first zone clear; "
            f"≥60% required)"
        )
    if n_obs > 0:
        limits.append(
            f"{n_obs} terrain obstruction(s) up to {max_obs:.0f} m above the "
            "line-of-sight"
        )
    if dist_km > 15 and f_ghz > 2.0:
        limits.append(
            f"long path ({dist_km:.1f} km) at {f_ghz:.2f} GHz — free-space loss "
            "dominates"
        )
    elif dist_km > 25:
        limits.append(f"long path ({dist_km:.1f} km) — free-space loss dominates")
    if slope > 50:
        limits.append(f"steep terrain ({slope:.0f} m/km slope)")

    if not limits:
        limits.append("no significant RF impairments detected on this path")

    # Concrete remediation
    if min_fres < 0.6 or n_obs > 0:
        remediation = (
            f"Raise the transmitter mast (currently {tx_h:.0f} m AGL) or relocate "
            "to a site with clearer line-of-sight."
        )
    elif sig < -95 and f_ghz > 2.5:
        remediation = (
            "Switch to a lower frequency band (sub-GHz) or add a higher-gain "
            "directional antenna at the receiver."
        )
    elif sig < -95:
        remediation = (
            "Increase EIRP (higher tx power or antenna gain) or deploy a "
            "repeater at roughly the midpoint of the link."
        )
    else:
        remediation = (
            "Link is healthy; monitor for seasonal foliage and adjacent-cell "
            "interference."
        )

    verdict = "Feasible" if feasible else "Not feasible"
    return (
        f"{verdict} link at {sig:.1f} dBm over {dist_km:.1f} km @ {f_ghz:.2f} GHz. "
        f"Expected service: {tier}. "
        f"Limiting factors: {'; '.join(limits)}. "
        f"Recommendation: {remediation}"
    )


def explain(prediction: Dict[str, Any]) -> str:
    """Return a natural-language explanation, preferring Bedrock when available.

    Always returns a string — falls back to the deterministic template if the
    LLM is not configured / not authorized / errors out. This guarantees that
    the public ``?explain=true`` flag works regardless of Bedrock availability.
    """
    out = explain_with_bedrock(prediction)
    if out:
        return out
    return explain_locally(prediction)


# ---------------------------------------------------------------------------
# Physics fallback (mirrors LinkEngine but kept self-contained so the module
# is importable from training/CLI environments without FastAPI)
# ---------------------------------------------------------------------------

def _physics_fallback(features: np.ndarray) -> float:
    """Recover an FSPL-only estimate from the feature vector."""
    log_d = features[_FEATURE_NAMES.index("log_d_km")]
    log_f_ghz = features[_FEATURE_NAMES.index("log_f_ghz")]
    tx_p = features[_FEATURE_NAMES.index("tx_power_dbm")]
    tx_g = features[_FEATURE_NAMES.index("tx_gain_dbi")]
    rx_g = features[_FEATURE_NAMES.index("rx_gain_dbi")]
    min_ratio = features[_FEATURE_NAMES.index("min_fresnel_ratio")]

    d_m = math.exp(log_d) * 1000
    f_hz = math.exp(log_f_ghz) * 1e9
    fspl = 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55
    rssi = tx_p + tx_g + rx_g - fspl
    if min_ratio < 0.6:
        rssi -= (0.6 - min_ratio) * 10
    return rssi


# ---------------------------------------------------------------------------
# Public prediction API
# ---------------------------------------------------------------------------

@dataclass
class PredictionResult:
    signal_dbm: float
    feasible: bool
    confidence: float        # 0-1, based on model RMSE
    source: str              # "sagemaker" | "local-model" | "physics-fallback"
    model_version: str
    features: Dict[str, float]
    # ── Optional clutter metadata (MapBiomas LULC, added 2026-05) ────────
    # ``None`` when no raster is configured or rx coordinates were not
    # supplied; otherwise the integer MapBiomas class code + human label
    # at the receiver location. Future training runs can incorporate
    # this as a one-hot feature; today it is exposed as observability.
    clutter_class: Optional[int] = None
    clutter_label: Optional[str] = None


def predict_signal(
    *,
    d_km: float,
    f_hz: float,
    tx_h_m: float,
    rx_h_m: float,
    tx_power_dbm: float = 43.0,
    tx_gain_dbi: float = 17.0,
    rx_gain_dbi: float = 12.0,
    terrain_profile: Optional[Sequence[float]] = None,
    tx_ground_elev_m: float = 0.0,
    rx_ground_elev_m: float = 0.0,
    feasibility_threshold_dbm: float = -95.0,
    rx_lat: Optional[float] = None,
    rx_lon: Optional[float] = None,
) -> PredictionResult:
    """Predict received signal strength for a single tx → rx link.

    When ``rx_lat`` and ``rx_lon`` are provided and a MapBiomas raster
    is configured (``MAPBIOMAS_RASTER_PATH``), the result is annotated
    with the LULC clutter class at the receiver — best-effort, never
    raises.

    If the loaded model artefact was trained with clutter
    (``feature_names`` length > 17), the rx coords are also injected
    into the feature vector. Older v1 artefacts ignore the coords.
    """
    feats = build_features(
        d_km=d_km, f_hz=f_hz, tx_h_m=tx_h_m, rx_h_m=rx_h_m,
        tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi,
        rx_gain_dbi=rx_gain_dbi, terrain_profile=terrain_profile,
        tx_ground_elev_m=tx_ground_elev_m, rx_ground_elev_m=rx_ground_elev_m,
    )

    def _features_for(model_obj: Any) -> np.ndarray:
        """Return ``feats`` (re)built to match the model's expected dim."""
        expected = getattr(model_obj, "feature_names", None)
        if expected is None or len(expected) <= len(_FEATURE_NAMES):
            return feats
        return build_features(
            d_km=d_km, f_hz=f_hz, tx_h_m=tx_h_m, rx_h_m=rx_h_m,
            tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi,
            rx_gain_dbi=rx_gain_dbi, terrain_profile=terrain_profile,
            tx_ground_elev_m=tx_ground_elev_m,
            rx_ground_elev_m=rx_ground_elev_m,
            with_clutter=True, rx_lat=rx_lat, rx_lon=rx_lon,
        )

    rssi: Optional[float] = None
    source = "physics-fallback"
    version = "physics-v1"
    confidence = 0.4

    sm = _predict_sagemaker(feats)
    if sm is not None:
        rssi = sm
        source = "sagemaker"
        version = f"sagemaker:{SAGEMAKER_ENDPOINT}"
        confidence = 0.85
    else:
        # Prefer band-aware (per-frequency) ridge when configured —
        # path-loss exponent and shadowing differ enough between 700 MHz
        # and 3.5 GHz that a single global model averages them poorly.
        band_model = get_band_model()
        if band_model is not None:
            picked, band_used = band_model.pick(f_hz)
            if picked is not None:
                rssi = picked.predict(_features_for(picked))
                source = "local-model-band"
                version = f"{picked.version}:band-{band_used}MHz"
                confidence = max(
                    0.3, min(0.9, 1.0 - (picked.rmse_db - 8.0) / 20.0)
                )
        if rssi is None:
            model = get_model()
            if model is not None:
                rssi = model.predict(_features_for(model))
                source = "local-model"
                version = model.version
                # 1 σ ≈ rmse; map to 0..1 confidence. Anchor on realistic sub-GHz
                # NLOS propagation accuracy (Hata/Okumura class): ≤ 8 dB → 0.9
                # (excellent), 13 dB → 0.75 (good), ≥ 18 dB → 0.4 (poor).
                confidence = max(0.3, min(0.9, 1.0 - (model.rmse_db - 8.0) / 20.0))

    if rssi is None:
        rssi = _physics_fallback(feats)

    rssi = float(np.clip(rssi, _FLOOR_DBM, 30.0))
    feature_dict = {name: float(feats[i]) for i, name in enumerate(_FEATURE_NAMES)}

    clutter_code: Optional[int] = None
    clutter_label_v: Optional[str] = None
    if rx_lat is not None and rx_lon is not None:
        try:
            from mapbiomas_clutter import (
                clutter_class_to_label,
                get_extractor,
            )
            clutter_code = get_extractor().get_clutter_class(rx_lat, rx_lon)
            if clutter_code is not None:
                clutter_label_v = clutter_class_to_label(clutter_code)
        except Exception as exc:  # noqa: BLE001 — clutter is best-effort
            logger.debug("mapbiomas lookup failed: %s", exc)

    return PredictionResult(
        signal_dbm=round(rssi, 2),
        feasible=rssi >= feasibility_threshold_dbm,
        confidence=round(confidence, 2),
        source=source,
        model_version=version,
        features=feature_dict,
        clutter_class=clutter_code,
        clutter_label=clutter_label_v,
    )


# ---------------------------------------------------------------------------
# Coverage grid
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class GridPoint:
    lat: float
    lon: float
    signal_dbm: float
    feasible: bool


async def predict_coverage_grid(
    *,
    tx_lat: float,
    tx_lon: float,
    tx_h_m: float,
    f_hz: float,
    bbox: Tuple[float, float, float, float],   # (min_lat, min_lon, max_lat, max_lon)
    grid_size: int = 25,
    rx_h_m: float = 10.0,
    tx_power_dbm: float = 43.0,
    tx_gain_dbi: float = 17.0,
    rx_gain_dbi: float = 12.0,
    elevation_service: Optional[Any] = None,
    feasibility_threshold_dbm: float = -95.0,
) -> List[GridPoint]:
    """Generate a coverage map by predicting RSSI at each grid cell.

    ``elevation_service`` should expose ``async get_profile(lat1, lon1,
    lat2, lon2) -> List[float]`` (the platform's :class:`ElevationService`
    satisfies this).  When ``None`` we fall back to a flat profile,
    which still exercises the FSPL portion of the model.
    """
    if grid_size < 2 or grid_size > 200:
        raise ValueError("grid_size must be in [2, 200]")

    min_lat, min_lon, max_lat, max_lon = bbox
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox must be (min_lat, min_lon, max_lat, max_lon)")

    lats = np.linspace(min_lat, max_lat, grid_size)
    lons = np.linspace(min_lon, max_lon, grid_size)

    # Fan out elevation fetches concurrently.
    coords: List[Tuple[float, float]] = [(float(la), float(lo)) for la in lats for lo in lons]

    async def _profile(rx_lat: float, rx_lon: float) -> List[float]:
        if elevation_service is None:
            return []
        try:
            return await elevation_service.get_profile(tx_lat, tx_lon, rx_lat, rx_lon)
        except Exception:
            # NOTE: do not log rx_lat / rx_lon — a debug log of every receiver
            # coordinate would replicate the same competitive-intelligence
            # leak that audit_log.hmac_target() guards against.
            logger.debug("elevation profile failed (coords redacted)", exc_info=True)
            return []

    import asyncio
    profiles = await asyncio.gather(*[_profile(la, lo) for la, lo in coords])

    out: List[GridPoint] = []
    for (la, lo), profile in zip(coords, profiles):
        d_km = haversine_km(tx_lat, tx_lon, la, lo)
        if d_km < 0.05:
            out.append(GridPoint(lat=la, lon=lo, signal_dbm=tx_power_dbm + tx_gain_dbi + rx_gain_dbi, feasible=True))
            continue
        tx_ground = profile[0] if profile else 0.0
        rx_ground = profile[-1] if profile else 0.0
        result = predict_signal(
            d_km=d_km, f_hz=f_hz, tx_h_m=tx_h_m, rx_h_m=rx_h_m,
            tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi,
            rx_gain_dbi=rx_gain_dbi, terrain_profile=profile,
            tx_ground_elev_m=tx_ground, rx_ground_elev_m=rx_ground,
            feasibility_threshold_dbm=feasibility_threshold_dbm,
        )
        out.append(GridPoint(
            lat=la, lon=lo, signal_dbm=result.signal_dbm, feasible=result.feasible
        ))
    return out


# ---------------------------------------------------------------------------
# Streaming coverage grid (SSE / async generator)
# ---------------------------------------------------------------------------

def grid_size_for_cell_size(
    bbox: Tuple[float, float, float, float],
    cell_size_m: float,
    *,
    max_cells_per_side: int = 200,
) -> int:
    """Return a grid_size (cells per side) producing ~``cell_size_m`` resolution.

    Uses the longer side of the bbox so each cell is at most ``cell_size_m``.
    Capped at ``max_cells_per_side`` so a sloppy bbox cannot produce a 10k-side
    grid that would DoS the elevation backend.
    """
    if cell_size_m <= 0:
        raise ValueError("cell_size_m must be positive")
    min_lat, min_lon, max_lat, max_lon = bbox
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox must be (min_lat, min_lon, max_lat, max_lon)")
    mid_lat = 0.5 * (min_lat + max_lat)
    side_lat_m = (max_lat - min_lat) * 111_320.0
    side_lon_m = (max_lon - min_lon) * 111_320.0 * math.cos(math.radians(mid_lat))
    side_m = max(side_lat_m, side_lon_m)
    n = int(math.ceil(side_m / cell_size_m)) + 1
    return max(2, min(max_cells_per_side, n))


async def predict_coverage_grid_stream(
    *,
    tx_lat: float,
    tx_lon: float,
    tx_h_m: float,
    f_hz: float,
    bbox: Tuple[float, float, float, float],
    grid_size: int = 25,
    rx_h_m: float = 10.0,
    tx_power_dbm: float = 43.0,
    tx_gain_dbi: float = 17.0,
    rx_gain_dbi: float = 12.0,
    elevation_service: Optional[Any] = None,
    feasibility_threshold_dbm: float = -95.0,
    concurrency: int = 16,
):
    """Async generator yielding ``GridPoint`` rows as they're predicted.

    Same parameters as :func:`predict_coverage_grid` but streams results so
    a UI can render a heatmap progressively. Uses a bounded semaphore to
    keep elevation-service fan-out under control even for 200x200 grids
    (40k cells).
    """
    if grid_size < 2 or grid_size > 200:
        raise ValueError("grid_size must be in [2, 200]")
    min_lat, min_lon, max_lat, max_lon = bbox
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise ValueError("bbox must be (min_lat, min_lon, max_lat, max_lon)")

    import asyncio
    lats = np.linspace(min_lat, max_lat, grid_size)
    lons = np.linspace(min_lon, max_lon, grid_size)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(la: float, lo: float) -> "GridPoint":
        async with sem:
            d_km = haversine_km(tx_lat, tx_lon, la, lo)
            if d_km < 0.05:
                return GridPoint(
                    lat=la, lon=lo,
                    signal_dbm=tx_power_dbm + tx_gain_dbi + rx_gain_dbi,
                    feasible=True,
                )
            profile: List[float] = []
            if elevation_service is not None:
                try:
                    profile = await elevation_service.get_profile(tx_lat, tx_lon, la, lo)
                except Exception:
                    logger.debug("elevation profile failed", exc_info=True)
            tx_ground = profile[0] if profile else 0.0
            rx_ground = profile[-1] if profile else 0.0
            result = predict_signal(
                d_km=d_km, f_hz=f_hz, tx_h_m=tx_h_m, rx_h_m=rx_h_m,
                tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi,
                rx_gain_dbi=rx_gain_dbi, terrain_profile=profile,
                tx_ground_elev_m=tx_ground, rx_ground_elev_m=rx_ground,
                feasibility_threshold_dbm=feasibility_threshold_dbm,
            )
            return GridPoint(
                lat=float(la), lon=float(lo),
                signal_dbm=result.signal_dbm, feasible=result.feasible,
            )

    tasks = [
        asyncio.create_task(_one(float(la), float(lo)))
        for la in lats for lo in lons
    ]
    try:
        for coro in asyncio.as_completed(tasks):
            yield await coro
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


# ---------------------------------------------------------------------------
# CLI: `python -m coverage_predict train`
# ---------------------------------------------------------------------------

if __name__ == "__main__":   # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Coverage prediction model trainer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_train = sub.add_parser("train", help="Train and persist the local model")
    p_train.add_argument("--n", type=int, default=5000, help="synthetic sample count")
    p_train.add_argument("--out", default=MODEL_PATH)
    p_train.add_argument("--l2", type=float, default=1.0)
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument(
        "--with-observations", action="store_true",
        help="Include rows from the link_observations table as training labels.",
    )

    p_show = sub.add_parser("info", help="Show metadata for the persisted model")

    p_train_bands = sub.add_parser(
        "train-bands",
        help="Train a per-band ridge for each nominal cellular band",
    )
    p_train_bands.add_argument("--n", type=int, default=8000)
    p_train_bands.add_argument("--out-dir", default="band_models")
    p_train_bands.add_argument("--l2", type=float, default=1.0)
    p_train_bands.add_argument("--seed", type=int, default=42)
    p_train_bands.add_argument(
        "--with-observations", action="store_true",
        help="Include rows from link_observations as additional labels.",
    )
    p_train_bands.add_argument(
        "--no-global-fallback", action="store_true",
        help="Skip the all-data fallback ridge (saves ~1 s).",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "train":
        historical = None
        if args.with_observations:
            historical = load_historical_from_stores(
                include_observations=args.with_observations,
            )
            print(f"Loaded {len(historical)} historical samples "
                  f"(observations={args.with_observations})")
        m = train_model(
            n_synthetic=args.n, l2=args.l2, seed=args.seed,
            save_to=args.out, historical=historical,
        )
        print(f"Trained model: rmse={m.rmse_db:.2f} dB, n_train={m.n_train}, saved to {args.out}")
    elif args.cmd == "info":
        m = get_model(refresh=True)
        if m is None:
            print(f"No model at {MODEL_PATH}")
        else:
            print(json.dumps({
                "version": m.version,
                "trained_at": m.trained_at,
                "rmse_db": m.rmse_db,
                "n_train": m.n_train,
                "feature_names": list(_FEATURE_NAMES),
            }, indent=2, default=str))
    elif args.cmd == "train-bands":
        historical = None
        if args.with_observations:
            historical = load_historical_from_stores(include_observations=True)
            print(f"Loaded {len(historical)} historical samples")
        ba = train_band_aware_model(
            n_synthetic=args.n,
            l2=args.l2,
            seed=args.seed,
            save_to_dir=args.out_dir,
            historical=historical,
            train_global_fallback=not args.no_global_fallback,
        )
        print(json.dumps(ba.info(), indent=2, default=str))
        print(f"Saved {len(ba.models)} band models to {args.out_dir}")
