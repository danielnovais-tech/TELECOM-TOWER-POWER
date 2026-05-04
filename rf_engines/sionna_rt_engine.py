# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Sionna RT 2.x — full 3D ray-tracing engine (mmWave-capable).

Status
------
**Tijolo 9 (2026-05-04) — feature-flag gated.** The engine is
``is_available()=True`` when all three conditions are met:

1. ``$SIONNA_RT_DISABLED`` is ``0`` / ``false`` / ``no`` (default
   ``1`` — disabled).
2. ``$SIONNA_RT_SCENE_PATH`` points to a directory that holds a
   valid ``scene.xml`` and ``manifest.json`` (produced by
   ``scripts/build_mitsuba_scene.py``).
3. ``mitsuba`` and ``sionna_rt`` are importable (GPU stack installed).

When available, ``predict_basic_loss`` runs a 1×1 raster (a single
receiver pixel centred on ``(phi_r, lam_r)``) via the T8
``_SionnaRtTracer`` and returns the loss for that pixel. The full
per-tile raster path (SQS async, ``POST /coverage/engines/sionna-rt/raster``)
is unchanged and remains the preferred route for large requests.

Distinction from the existing ``sionna`` engine
-----------------------------------------------
The legacy ``sionna`` adapter (``sionna_engine.py``) is a learned
**MLP / TFLite** path-loss predictor — Sionna 1.x era, CPU-friendly,
trained on drive-test rows. It stays put.

This new ``sionna-rt`` adapter targets Sionna 2.x's
`sionna.rt` module (Mitsuba 3 + Dr.Jit kernel), which performs
deterministic 3D ray launching against a textured scene with
frequency-dependent material parameters — the only path in the
stack that is physically meaningful at FR2 / mmWave (24-100 GHz)
where diffraction-free ITU-R P.1812 over-predicts coverage by
20+ dB indoors / urban canyons.

Planned runtime requirements (Q2/2026)
--------------------------------------
* CUDA 12.x + an Ampere-or-newer GPU (compute capability ≥ 8.0).
* ``sionna >= 2.0``, ``mitsuba >= 3.5``, ``drjit``, ``torch >= 2.4``.
  Pinned in a separate ``requirements-gpu.txt`` to keep the CPU
  API container slim.
* A Mitsuba scene file (``$SIONNA_RT_SCENE_PATH``) prepared by the
  yet-to-be-written ``scripts/build_mitsuba_scene.py`` (OSM
  buildings → triangulated meshes + ITU-R P.2040 material tags +
  SRTM terrain backdrop).

Deployment shape
----------------
The CPU-only API container will *not* call this engine inline.
A separate AWS Batch / EC2 G-instance worker pool consumes
``coverage:rt`` jobs from SQS, runs the trace on GPU, and writes
the resulting per-pixel loss raster to S3. The HTTP path is then
only a "kick off and poll" layer; ``predict_basic_loss`` for a
single link is a thin wrapper over a single-pixel trace and is
expected to be the slowest engine in the stack by ~2 orders of
magnitude.

See ``docs/rf-engines.md`` (§ Sionna RT 2.x — full 3D ray-tracing,
Q2/2026 roadmap) for the full design and the ``ROADMAP.md``
milestone entry for the delivery checklist.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)


def _is_disabled() -> bool:
    """Default-on disable flag. ``SIONNA_RT_DISABLED=0`` opt-in."""
    return os.getenv("SIONNA_RT_DISABLED", "1").lower() in {"1", "true", "yes"}


def _scene_path() -> str:
    return os.getenv("SIONNA_RT_SCENE_PATH", "")


def _has_gpu_stack() -> bool:
    """Return True if mitsuba + sionna_rt are importable."""
    for mod in ("mitsuba", "sionna_rt"):
        if mod not in sys.modules:
            try:
                __import__(mod)
            except ImportError:
                return False
    return True


class SionnaRTEngine(RFEngine):
    """3D ray-tracing path-loss engine — Sionna RT 2.x, Mitsuba 3.

    Availability (all three required):
    * ``$SIONNA_RT_DISABLED=0`` (default: ``1``).
    * ``$SIONNA_RT_SCENE_PATH`` points to a directory with a valid
      ``scene.xml`` + ``manifest.json``.
    * ``mitsuba`` and ``sionna_rt`` importable.

    When available, ``predict_basic_loss`` fires a 1×1 raster
    centred on ``(phi_r, lam_r)`` via the T8 ``_SionnaRtTracer``
    and returns the single-pixel loss. The cell is sized so the
    receiver pixel spans ±250 m around the RX point (the minimum
    meaningful resolution for a Mitsuba city-scale scene; finer
    cells just alias to the nearest mesh triangle).
    """

    name = "sionna-rt"

    def is_available(self) -> bool:
        if _is_disabled():
            return False
        scene = _scene_path()
        if not scene:
            return False
        if not os.path.isfile(os.path.join(scene, "scene.xml")):
            return False
        if not os.path.isfile(os.path.join(scene, "manifest.json")):
            return False
        return _has_gpu_stack()

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
        """Single-link prediction: 1×1 raster at the receiver location.

        Unused link-profile arguments (``d_km``, ``h_m``,
        ``clutter_heights_m``, ``pol``, ``zone``, ``time_pct``,
        ``loc_pct``) are accepted for interface compatibility but
        discarded — the ray tracer derives all geometry from the
        scene file and the Tx/Rx lat/lon pair.
        """
        try:
            return self._run_trace(
                f_hz=f_hz, htg=htg, hrg=hrg,
                phi_t=phi_t, lam_t=lam_t,
                phi_r=phi_r, lam_r=lam_r,
            )
        except Exception:
            logger.exception("sionna-rt predict_basic_loss failed; returning None")
            return None

    def _run_trace(
        self,
        *,
        f_hz: float,
        htg: float,
        hrg: float,
        phi_t: float,
        lam_t: float,
        phi_r: float,
        lam_r: float,
    ) -> Optional[LossEstimate]:
        # Lazy import here so the module stays importable on CPU hosts.
        import sys as _sys
        _worker_mod = _sys.modules.get("scripts.sionna_rt_worker")
        if _worker_mod is None:
            import importlib
            _worker_mod = importlib.import_module("scripts.sionna_rt_worker")

        tracer = _worker_mod._SionnaRtTracer()

        # Build a minimal 1-cell Job that places the receiver pixel
        # directly at (phi_r, lam_r).  The bbox spans ±_CELL_HALF_DEG
        # so the cell covers a ~500 m × 500 m footprint — wide enough
        # to avoid the pixel straddling the scene edge on any input.
        _CELL_HALF_DEG = 0.0025  # ≈ 250 m at equator
        job = _worker_mod.Job(
            job_id="inline-predict",
            scene_s3_uri="",         # not used for local traces
            result_s3_uri="",        # not used
            tx_lat=float(phi_t),
            tx_lon=float(lam_t),
            tx_height_m=float(htg),
            tx_power_dbm=43.0,       # irrelevant — path loss is geometry
            frequency_hz=float(f_hz),
            rows=1,
            cols=1,
            bbox_south=float(phi_r) - _CELL_HALF_DEG,
            bbox_west=float(lam_r) - _CELL_HALF_DEG,
            bbox_north=float(phi_r) + _CELL_HALF_DEG,
            bbox_east=float(lam_r) + _CELL_HALF_DEG,
        )

        arr = tracer.trace(_scene_path(), job)
        loss_db = float(arr[0, 0])
        return LossEstimate(
            basic_loss_db=loss_db,
            engine=self.name,
            confidence=1.0,
            extra={
                "rx_height_m": float(os.getenv("SIONNA_RT_RX_HEIGHT_M", "1.5")),
                "scene_path": _scene_path(),
            },
        )


register_engine(SionnaRTEngine())

