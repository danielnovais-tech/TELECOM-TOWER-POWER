# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Learned propagation engine — TFLite inference path.

Loads the artefact pair produced by ``scripts/train_sionna.py``:

* ``$SIONNA_MODEL_PATH``         (default ``/srv/models/sionna_model.tflite``)
* ``$SIONNA_FEATURES_PATH``      (default: same dir, ``sionna_features.json``)

The sidecar JSON pins the feature schema version + per-feature
``mean``/``std``. We refuse to serve predictions if the schema doesn't
match :data:`rf_engines._sionna_features.FEATURE_SCHEMA_VERSION` —
silently serving with a stale schema would yield plausible-looking
but systematically biased path losses, which is worse than failing
closed (the registry simply skips this engine and the platform
falls back to P.1812).

Runtime requirements
--------------------
* ``tflite_runtime`` *or* full TensorFlow installed (we try the
  lightweight runtime first; that's all the API container ships).
* numpy (already in base image).

Disabling
---------
Set ``SIONNA_DISABLED=1`` (default) until ops has provisioned a
benchmarked artefact. ``coverage_diff_robot`` will pick it up once
enabled and alerts on drift > 3 dB vs. P.1812 on the golden link set.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from . import register_engine
from ._sionna_features import (
    FEATURE_DIM,
    FEATURE_SCHEMA_VERSION,
    build_features,
)
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "/srv/models/sionna_model.tflite"


def _model_path() -> str:
    return os.getenv("SIONNA_MODEL_PATH", _DEFAULT_MODEL)


def _features_path() -> str:
    explicit = os.getenv("SIONNA_FEATURES_PATH")
    if explicit:
        return explicit
    return str(Path(_model_path()).with_name("sionna_features.json"))


def _is_disabled() -> bool:
    return os.getenv("SIONNA_DISABLED", "1").lower() in {"1", "true", "yes"}


def _load_interpreter(model_path: str):
    """Load a TFLite interpreter, preferring the lightweight runtime.

    Returns ``None`` if neither runtime is importable; the caller
    treats that as ``is_available() == False``.
    """
    # tflite_runtime is ~5 MB; tensorflow is ~600 MB. The API container
    # should ship the former; the trainer / dev boxes have full TF.
    try:  # pragma: no cover — exercised only with the dep installed
        from tflite_runtime.interpreter import Interpreter  # type: ignore[import-not-found]
        return Interpreter(model_path=model_path)
    except Exception:
        pass
    try:  # pragma: no cover
        import tensorflow as tf  # type: ignore[import-not-found]
        return tf.lite.Interpreter(model_path=model_path)
    except Exception:
        logger.debug("no tflite runtime available", exc_info=True)
        return None


class SionnaEngine(RFEngine):
    """ML-based path-loss predictor.

    Lazy-loads the model on the first :meth:`is_available` call.
    Failures latch — we don't keep retrying every 30 ms in the compare
    endpoint. Call :meth:`reset` to force a re-load (used by tests).
    """

    name = "sionna"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tried_load = False
        self._interp = None
        self._in_idx: Optional[int] = None
        self._out_idx: Optional[int] = None
        self._mean: Optional[np.ndarray] = None
        self._std: Optional[np.ndarray] = None
        self._meta: dict = {}

    def reset(self) -> None:
        """Drop the cached interpreter so the next call reloads.

        Useful in tests that mutate ``SIONNA_*`` env vars between cases.
        Not exposed via the public RFEngine contract on purpose.
        """
        with self._lock:
            self._tried_load = False
            self._interp = None
            self._mean = None
            self._std = None
            self._meta = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> bool:
        if self._tried_load:
            return self._interp is not None
        with self._lock:
            if self._tried_load:
                return self._interp is not None
            self._tried_load = True

            if _is_disabled():
                logger.debug("SIONNA_DISABLED=1; engine off")
                return False

            mpath = _model_path()
            spath = _features_path()
            if not os.path.isfile(mpath):
                logger.info("sionna model missing at %s", mpath)
                return False
            if not os.path.isfile(spath):
                logger.warning(
                    "sionna sidecar missing at %s; refusing to serve "
                    "(would be unsafe without normalisation stats)",
                    spath,
                )
                return False

            try:
                meta = json.loads(Path(spath).read_text())
            except Exception:
                logger.exception("sionna sidecar unreadable")
                return False

            schema = meta.get("schema_version")
            if schema != FEATURE_SCHEMA_VERSION:
                # Fail closed — see module docstring.
                logger.error(
                    "sionna schema mismatch: artefact=%s runtime=%s; "
                    "retrain the model before re-enabling",
                    schema, FEATURE_SCHEMA_VERSION,
                )
                return False
            dim = int(meta.get("feature_dim", -1))
            if dim != FEATURE_DIM:
                logger.error(
                    "sionna feature_dim mismatch: artefact=%d runtime=%d",
                    dim, FEATURE_DIM,
                )
                return False
            mean = np.asarray(meta.get("mean", []), dtype=np.float32)
            std = np.asarray(meta.get("std", []), dtype=np.float32)
            if mean.shape != (FEATURE_DIM,) or std.shape != (FEATURE_DIM,):
                logger.error("sionna sidecar mean/std shape wrong")
                return False
            # Guard against zeros sneaking through a hand-edited sidecar
            # (the trainer already replaces σ<1e-6 with 1.0, but defence
            # in depth — division by zero at inference would NaN-poison
            # every downstream prediction).
            std = np.where(std < 1e-6, np.float32(1.0), std)

            interp = _load_interpreter(mpath)
            if interp is None:
                return False
            try:
                interp.allocate_tensors()
                in_details = interp.get_input_details()
                out_details = interp.get_output_details()
                if not in_details or not out_details:
                    return False
                in_shape = tuple(in_details[0]["shape"])
                if in_shape[-1] != FEATURE_DIM:
                    logger.error(
                        "tflite input dim %s != %d", in_shape, FEATURE_DIM,
                    )
                    return False
            except Exception:
                logger.exception("sionna interpreter init failed")
                return False

            self._interp = interp
            self._in_idx = in_details[0]["index"]
            self._out_idx = out_details[0]["index"]
            self._mean = mean
            self._std = std
            self._meta = meta
            logger.info(
                "sionna engine loaded: model=%s schema=%s n_train=%s",
                mpath, schema, meta.get("n_train"),
            )
            return True

    # ------------------------------------------------------------------
    # RFEngine API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._load()

    def predict_basic_loss(
        self,
        *,
        f_hz: float,
        d_km: Sequence[float],
        h_m: Sequence[float],
        htg: float,
        hrg: float,
        phi_t: float,
        lam_t: float,
        phi_r: float,
        lam_r: float,
        clutter_heights_m: Optional[Sequence[float]] = None,  # noqa: ARG002 — clutter
        # is sourced from MapBiomas inside build_features. Accepting the kwarg
        # keeps the engine signature compatible with the registry contract.
        pol: Optional[int] = None,
        zone: Optional[int] = None,
        time_pct: Optional[float] = None,  # noqa: ARG002
        loc_pct: Optional[float] = None,   # noqa: ARG002
    ) -> Optional[LossEstimate]:
        if not self._load():
            return None
        assert self._interp is not None and self._mean is not None
        assert self._std is not None and self._in_idx is not None

        try:
            feats = build_features(
                f_hz=f_hz, d_km=d_km, h_m=h_m, htg=htg, hrg=hrg,
                phi_t=phi_t, lam_t=lam_t, phi_r=phi_r, lam_r=lam_r,
                pol=pol, zone=zone,
            )
        except Exception:
            logger.debug("feature build failed", exc_info=True)
            return None

        x = ((feats.astype(np.float32) - self._mean) / self._std)
        x = x.reshape(1, FEATURE_DIM).astype(np.float32)

        t0 = time.perf_counter()
        # tflite_runtime Interpreter is NOT thread-safe — the lock protects
        # both ``set_tensor`` and ``invoke``. The platform's compare
        # endpoint dispatches engines in a thread pool, so this matters
        # in production even though tests are single-threaded.
        with self._lock:
            try:
                self._interp.set_tensor(self._in_idx, x)
                self._interp.invoke()
                y = self._interp.get_tensor(self._out_idx)
            except Exception:
                logger.exception("sionna invoke failed")
                return None
        runtime_ms = (time.perf_counter() - t0) * 1000.0

        try:
            lb = float(np.asarray(y).reshape(-1)[0])
        except Exception:
            return None
        # Sanity gate: anything outside [40, 250] dB is an extrapolation
        # disaster (model saw nothing remotely like this link in training).
        # Better to return None and let the registry fall back to P.1812.
        if not np.isfinite(lb) or lb < 40.0 or lb > 250.0:
            logger.warning("sionna predicted Lb=%.1f dB out of range; suppressed", lb)
            return None

        return LossEstimate(
            basic_loss_db=lb,
            engine=self.name,
            confidence=0.7,  # learned model — never report 1.0
            runtime_ms=runtime_ms,
            extra={
                "schema_version": FEATURE_SCHEMA_VERSION,
                "n_train": self._meta.get("n_train"),
                "trained_at": self._meta.get("trained_at"),
            },
        )


register_engine(SionnaEngine())
