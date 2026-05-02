# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""NVIDIA Sionna-based learned propagation engine (scaffolding).

This adapter intentionally ships *without* a trained model bundled.
Sionna requires a CUDA-capable GPU and the training corpus we want
(SRTM + ANATEL drive-tests + MapBiomas clutter) is on the order of
gigabytes. The full pipeline lives in ``scripts/train_sionna.py``
and is run on dedicated infra (g5.xlarge spot fleet); the resulting
``sionna_model.tflite`` is uploaded to S3 under ``$SIONNA_MODEL_S3_URI``
and downloaded into the ECS task at boot.

The adapter is *available* iff:

* the ``sionna`` package imports cleanly (CPU fallback is fine for
  inference, GPU only required for training);
* a model artefact is present at ``SIONNA_MODEL_PATH`` (default
  ``/srv/models/sionna_model.tflite``);
* TensorFlow Lite (or the Sionna runtime) can load it.

Until those preconditions are met ``is_available()`` returns ``False``
and the registry simply skips this engine. **No production traffic
should rely on Sionna predictions until the model has been
benchmarked against P.1812 + drive-test ground truth.**

We ship the scaffolding now so the rest of the platform (router,
compare endpoint, GitHub Actions robot) treats Sionna as a
first-class engine the moment ops drops a model into S3.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)

_MODEL_PATH = os.getenv("SIONNA_MODEL_PATH", "/srv/models/sionna_model.tflite")
_DISABLED = os.getenv("SIONNA_DISABLED", "1").lower() in {"1", "true", "yes"}


class SionnaEngine(RFEngine):
    """Learned propagation predictor — disabled by default.

    To enable, set ``SIONNA_DISABLED=0`` and provision a model
    artefact at ``$SIONNA_MODEL_PATH``.
    """

    name = "sionna"

    def __init__(self) -> None:
        self._interp = None
        self._tried_load = False

    def _try_load(self) -> bool:
        if self._tried_load:
            return self._interp is not None
        self._tried_load = True
        if _DISABLED:
            return False
        if not os.path.isfile(_MODEL_PATH):
            return False
        try:
            # Lazy import — avoid pulling tensorflow into the cold-start
            # path of every API container.
            import tensorflow as tf  # type: ignore[import-not-found]

            self._interp = tf.lite.Interpreter(model_path=_MODEL_PATH)
            self._interp.allocate_tensors()
            return True
        except Exception:  # pragma: no cover
            logger.debug("Sionna model load failed", exc_info=True)
            self._interp = None
            return False

    def is_available(self) -> bool:
        return self._try_load()

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
        clutter_heights_m: Optional[Sequence[float]] = None,
        pol: Optional[int] = None,
        zone: Optional[int] = None,
        time_pct: Optional[float] = None,
        loc_pct: Optional[float] = None,
    ) -> Optional[LossEstimate]:
        if not self._try_load():
            return None
        # Stub: until the training script lands, return None so the
        # compare endpoint cleanly excludes this engine. Replace with
        # the real inference call once a model artefact ships.
        # See scripts/train_sionna.py for the planned feature schema.
        return None


register_engine(SionnaEngine())
