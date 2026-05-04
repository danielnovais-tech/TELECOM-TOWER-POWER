# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Sionna RT 2.x — full 3D ray-tracing engine (mmWave-capable).

Status
------
**Tijolo 11 (2026-05-04) — real single-link prediction.**
``predict_basic_loss`` now uses Sionna RT's *paths mode*: an explicit
``srt.Receiver`` is placed at ``(phi_r, lam_r, hrg)`` alongside a
``srt.Transmitter`` at ``(phi_t, lam_t, htg)``; ``PathSolver`` runs
deterministic ray launching and returns a ``Paths`` object; the total
path gain ``Σ|aᵢ|²`` is converted to basic loss in dB.

This replaces the T9 scaffold that delegated to the raster
``_SionnaRtTracer.trace()`` — a Coverage-Map path designed for large
tile jobs, not single-link predictions.  The key improvements:

* **``hrg`` is respected** — the receiver z-coordinate is set from the
  actual antenna height above ground, not a global env-var default.
* **Exact receiver coordinates** — no 500 m area averaging.
* **mmWave-correct** — deterministic ray paths capture LOS/NLOS
  transitions that a coverage-map cell at 28 GHz would smear out.
* **Scene bbox guard** — returns ``None`` (fail-closed) when the RX
  point is outside the scene, rather than emitting a spurious 300 dB
  sentinel from zero-path gain.

Availability (all three conditions required):

1. ``$SIONNA_RT_DISABLED=0`` (default: ``1``).
2. ``$SIONNA_RT_SCENE_PATH`` points to a directory with a valid
   ``scene.xml`` + ``manifest.json``.
3. ``mitsuba`` and ``sionna_rt`` importable.

The raster path (``POST /coverage/engines/sionna-rt/raster`` → SQS →
``_SionnaRtTracer.trace()``) is unchanged.  That path is preferred for
large coverage sweeps; ``predict_basic_loss`` is the per-link fast path
used by ``/coverage/engines/compare`` and the T10 validation gate.

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
    """Return True if mitsuba + sionna RT are importable.

    The PyPI ``sionna-rt`` 2.x wheel exposes the package as
    ``sionna.rt`` (sub-module of ``sionna``), but the original Sionna
    1.x convention used a top-level ``sionna_rt`` package — and some
    redistributions still ship under that name. We accept either:
    first try ``sionna.rt``, then fall back to ``sionna_rt``.
    """
    if "mitsuba" not in sys.modules:
        try:
            __import__("mitsuba")
        except ImportError:
            return False
    if "sionna_rt" in sys.modules or "sionna.rt" in sys.modules:
        return True
    try:
        __import__("sionna.rt")
        return True
    except ImportError:
        pass
    try:
        __import__("sionna_rt")
        return True
    except ImportError:
        return False


def _import_sionna_rt():
    """Return the active ``sionna.rt`` / ``sionna_rt`` module.

    Mirrors :func:`_has_gpu_stack` resolution order so engine code
    can do ``srt = _import_sionna_rt()`` once and use the same
    handle regardless of which wheel was installed.
    """
    try:
        import sionna.rt as srt  # type: ignore[import-not-found]
        return srt
    except ImportError:
        import sionna_rt as srt  # type: ignore[import-not-found]
        return srt


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
        """Single-link prediction via Sionna RT paths mode.

        Places an explicit ``srt.Transmitter`` at (phi_t, lam_t, htg) and an
        explicit ``srt.Receiver`` at (phi_r, lam_r, hrg), runs
        ``PathSolver`` without a coverage-map grid, and converts the total
        path gain ``Σ|aᵢ|²`` to basic loss in dB.

        This is fundamentally different from the raster/CoverageMap path in
        ``_SionnaRtTracer.trace()`` — that code is designed for large tile
        jobs (1000×1000 cells, SQS async queue).  For a single link we want:

        * exact receiver coordinates (respecting ``hrg``);
        * deterministic ray paths, not a Monte-Carlo area average;
        * a result that is physically meaningful at mmWave, where a 500 m
          coverage-map cell would average over LOS and deep-NLOS pixels.
        """
        import importlib
        import math

        import mitsuba as mi          # type: ignore[import-not-found]
        import numpy as np            # type: ignore[import-not-found]
        srt = _import_sionna_rt()

        # Worker helpers: manifest parsing, scene.xml lookup, variant selection.
        # Lazy import keeps this module importable on CPU hosts without numpy.
        _w = sys.modules.get("scripts.sionna_rt_worker")
        if _w is None:
            _w = importlib.import_module("scripts.sionna_rt_worker")

        scene_dir = _scene_path()
        manifest = _w._load_manifest(scene_dir)
        scene_xml = _w._resolve_scene_xml(scene_dir)

        # Guard: an RX outside the scene bbox produces degenerate path gains
        # (all rays miss the geometry).  Returning None is safer than emitting
        # a spurious 300 dB sentinel that might slip into A/B comparisons.
        bbox = manifest.get("bbox")  # [south, west, north, east]
        if bbox:
            s, w, n, e = (float(b) for b in bbox)
            if not (s <= phi_r <= n and w <= lam_r <= e):
                logger.warning(
                    "RX (%.6f, %.6f) is outside scene bbox "
                    "[%.4f, %.4f, %.4f, %.4f] — returning None",
                    phi_r, lam_r, s, w, n, e,
                )
                return None

        # ENU projection centred on the scene bbox midpoint — must match the
        # coordinate frame used by scripts/build_mitsuba_scene.py so that
        # TX / RX positions align with the loaded Mitsuba geometry.
        if bbox:
            lon0 = (float(bbox[1]) + float(bbox[3])) / 2.0
            lat0_c = (float(bbox[0]) + float(bbox[2])) / 2.0
        else:
            lon0 = (lam_t + lam_r) / 2.0
            lat0_c = (phi_t + phi_r) / 2.0
        R = 6_371_008.8
        cos_lat0 = math.cos(math.radians(lat0_c))

        def _proj(lat: float, lon: float, z: float) -> list:
            x = math.radians(lon - lon0) * R * cos_lat0
            y = math.radians(lat - lat0_c) * R
            return [x, y, float(z)]

        variant = _w._select_mitsuba_variant(mi)
        mi.set_variant(variant)

        scene = srt.load_scene(scene_xml)
        scene.frequency = float(f_hz)

        # Isotropic single-element arrays — path gain is antenna-independent.
        scene.tx_array = srt.PlanarArray(
            num_rows=1, num_cols=1, pattern="iso", polarization="V",
        )
        scene.rx_array = srt.PlanarArray(
            num_rows=1, num_cols=1, pattern="iso", polarization="V",
        )
        scene.add(srt.Transmitter(name="tx", position=_proj(phi_t, lam_t, float(htg))))
        scene.add(srt.Receiver(name="rx",    position=_proj(phi_r, lam_r, float(hrg))))

        max_depth = int(os.getenv("SIONNA_RT_MAX_DEPTH", "5"))
        solver = srt.PathSolver()
        paths = solver(scene=scene, max_depth=max_depth)

        # Σ|aᵢ|² over all paths → total linear path gain (dimensionless).
        # paths.a shape: [batch, num_rx, rx_ant, num_tx, tx_ant, max_paths]
        a = np.asarray(paths.a)
        path_gain = float(np.sum(np.abs(a) ** 2))
        path_gain = max(path_gain, 1e-30)  # clamp zero-path case → 300 dB cap

        loss_db = -10.0 * math.log10(path_gain)
        return LossEstimate(
            basic_loss_db=loss_db,
            engine=self.name,
            confidence=1.0,
            extra={
                "rx_height_m": float(hrg),
                "tx_height_m": float(htg),
                "scene_path": scene_dir,
                "mitsuba_variant": variant,
                "frequency_hz": float(f_hz),
            },
        )


register_engine(SionnaRTEngine())

