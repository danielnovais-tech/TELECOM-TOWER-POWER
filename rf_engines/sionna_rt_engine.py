# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Sionna RT 2.x — full 3D ray-tracing engine (mmWave-capable).

Status
------
**Roadmap Q2/2026 — scaffold only.** This module registers the engine
under the name ``sionna-rt`` so the registry, ``/coverage/engines``
listing, and the nightly compare robot all *know about* it, but the
adapter unconditionally reports ``is_available() == False`` until the
GPU-backed runtime is actually provisioned. ``predict_basic_loss``
returns ``None`` on every call — there is intentionally no CPU
fallback because path-loss extracted from a degenerate (no-bounce)
ray trace would be indistinguishable from FSPL and would mislead
the compare endpoint.

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
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)


def _is_disabled() -> bool:
    """Default-on disable flag.

    Stays at ``1`` even on the GPU image until ops has built and
    benchmarked a Mitsuba scene for the AOI being served.
    """
    return os.getenv("SIONNA_RT_DISABLED", "1").lower() in {"1", "true", "yes"}


def _scene_path() -> str:
    return os.getenv("SIONNA_RT_SCENE_PATH", "")


class SionnaRTEngine(RFEngine):
    """3D ray-tracing path-loss engine — Sionna 2.x roadmap scaffold.

    The class is intentionally a no-op until the GPU runtime lands.
    Keeping it registered (but unavailable) lets us:

    * surface a placeholder row in ``GET /coverage/engines`` so the
      operator UI can show a "coming Q2/2026" badge;
    * exercise the autoregister + compare plumbing in CI without any
      heavyweight deps;
    * ship the env-var contract (``SIONNA_RT_*``) ahead of the actual
      implementation so ops can pre-provision SSM parameters.
    """

    name = "sionna-rt"

    def is_available(self) -> bool:
        if _is_disabled():
            return False
        # Defence in depth: even if someone flips SIONNA_RT_DISABLED=0
        # on a CPU image, refuse to claim availability without a scene
        # file *and* an importable sionna.rt module. We probe lazily so
        # the import cost is paid once, not every compare call.
        if not _scene_path() or not os.path.isfile(_scene_path()):
            return False
        try:  # pragma: no cover — exercised only with the GPU dep set
            import sionna.rt  # type: ignore[import-not-found]  # noqa: F401
        except Exception:
            return False
        # Roadmap stub: even with deps + scene present we still return
        # False until the predict path is implemented. Flip this when
        # the Q2/2026 milestone lands.
        return False

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
        # Roadmap scaffold — never serve a number from this engine.
        # Returning None is the documented fail-closed contract; the
        # registry will simply skip the row in /coverage/engines/compare.
        del (
            f_hz, d_km, h_m, htg, hrg, phi_t, lam_t, phi_r, lam_r,
            clutter_heights_m, pol, zone, time_pct, loc_pct,
        )
        return None


register_engine(SionnaRTEngine())
