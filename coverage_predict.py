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
SAGEMAKER_ENDPOINT = os.getenv("SAGEMAKER_COVERAGE_ENDPOINT", "")
SAGEMAKER_REGION = os.getenv("SAGEMAKER_REGION", os.getenv("AWS_REGION", "us-east-1"))

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


def feature_names() -> Tuple[str, ...]:
    """Public, ordered feature names accepted by the model."""
    return _FEATURE_NAMES


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
) -> np.ndarray:
    """Return the engineered feature vector in canonical order."""
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
    return np.array([raw[k] for k in _FEATURE_NAMES], dtype=float)


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
    rmse_db: float = 0.0
    n_train: int = 0

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
            meta=np.array(
                json.dumps({
                    "version": self.version,
                    "trained_at": self.trained_at,
                    "rmse_db": self.rmse_db,
                    "n_train": self.n_train,
                    "feature_names": list(_FEATURE_NAMES),
                }),
                dtype=object,
            ),
        )
        logger.info("Saved coverage model to %s (rmse=%.2f dB, n=%d)",
                    path, self.rmse_db, self.n_train)

    @classmethod
    def load(cls, path: str) -> "CoverageModel":
        with np.load(path, allow_pickle=True) as npz:
            meta = json.loads(str(npz["meta"]))
            return cls(
                weights=npz["weights"],
                feature_mean=npz["feature_mean"],
                feature_std=npz["feature_std"],
                version=meta.get("version", "ridge-v1"),
                trained_at=meta.get("trained_at", 0.0),
                rmse_db=meta.get("rmse_db", 0.0),
                n_train=meta.get("n_train", 0),
            )


_model_cache: Optional[CoverageModel] = None
_model_loaded_at: float = 0.0


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


def train_model(
    n_synthetic: int = 5000,
    historical: Optional[Sequence[Tuple[np.ndarray, float]]] = None,
    l2: float = 1.0,
    seed: int = 42,
    save_to: Optional[str] = None,
) -> CoverageModel:
    """Train the ridge regression model and (optionally) persist it."""
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

    model = CoverageModel(
        weights=w,
        feature_mean=mean,
        feature_std=std_safe,
        rmse_db=rmse,
        n_train=len(X),
    )
    if save_to:
        model.save(save_to)
        # Force reload on next call
        global _model_cache
        _model_cache = model
    return model


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
) -> PredictionResult:
    """Predict received signal strength for a single tx → rx link."""
    feats = build_features(
        d_km=d_km, f_hz=f_hz, tx_h_m=tx_h_m, rx_h_m=rx_h_m,
        tx_power_dbm=tx_power_dbm, tx_gain_dbi=tx_gain_dbi,
        rx_gain_dbi=rx_gain_dbi, terrain_profile=terrain_profile,
        tx_ground_elev_m=tx_ground_elev_m, rx_ground_elev_m=rx_ground_elev_m,
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
        model = get_model()
        if model is not None:
            rssi = model.predict(feats)
            source = "local-model"
            version = model.version
            # 1 σ ≈ rmse; map to 0..1 confidence (≤ 6 dB → 0.9, ≥ 20 dB → 0.3)
            confidence = max(0.3, min(0.9, 1.0 - (model.rmse_db - 6.0) / 20.0))

    if rssi is None:
        rssi = _physics_fallback(feats)

    rssi = float(np.clip(rssi, _FLOOR_DBM, 30.0))
    feature_dict = {name: float(feats[i]) for i, name in enumerate(_FEATURE_NAMES)}

    return PredictionResult(
        signal_dbm=round(rssi, 2),
        feasible=rssi >= feasibility_threshold_dbm,
        confidence=round(confidence, 2),
        source=source,
        model_version=version,
        features=feature_dict,
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
    if grid_size < 2 or grid_size > 100:
        raise ValueError("grid_size must be in [2, 100]")

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
            logger.debug("elevation profile failed for %s,%s", rx_lat, rx_lon, exc_info=True)
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

    p_show = sub.add_parser("info", help="Show metadata for the persisted model")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "train":
        m = train_model(n_synthetic=args.n, l2=args.l2, seed=args.seed, save_to=args.out)
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
