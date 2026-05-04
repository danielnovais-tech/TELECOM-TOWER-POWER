# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""ITU-R P.2040-3 (Annex 1) material permittivity loader + evaluator.

The on-disk library is ``data/materials_p2040.json``. Each entry is
parameterised by the four-coefficient model from P.2040-3 Annex 1
Table 3:

    epsilon_r' = a * f_GHz ** b           (real part, dimensionless)
    sigma      = c * f_GHz ** d           (conductivity, S/m)
    epsilon_r''= sigma / (2 * pi * f_Hz * epsilon_0)

The library SHA-256 is recorded in ``manifest.json`` so a GPU worker
can refuse to trace a scene built against a stale material set
(silent corruption mode #1 for mmWave coverage estimates).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Vacuum permittivity, F/m. Pinned in the JSON too so the worker can
# verify; we read whichever one is in the file.
_DEFAULT_EPSILON_0 = 8.8541878128e-12

# Default library path \u2014 resolved relative to the repo root.
_DEFAULT_LIBRARY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "materials_p2040.json",
)


def load_library(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the JSON library from ``path`` (defaults to ``data/materials_p2040.json``)."""
    p = path or _DEFAULT_LIBRARY_PATH
    with open(p, "r", encoding="utf-8") as fh:
        lib = json.load(fh)
    _validate_library(lib)
    return lib


def library_sha256(path: Optional[str] = None) -> str:
    """Return the SHA-256 of the on-disk JSON file (used in manifests)."""
    p = path or _DEFAULT_LIBRARY_PATH
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_library(lib: Dict[str, Any]) -> None:
    """Cheap sanity checks \u2014 ``RuntimeError`` on schema drift."""
    if lib.get("schema_version") != 1:
        raise RuntimeError(
            f"Unsupported material library schema_version="
            f"{lib.get('schema_version')!r} (expected 1)"
        )
    materials = lib.get("materials")
    if not isinstance(materials, dict) or not materials:
        raise RuntimeError("material library has no 'materials' entries")
    for name, m in materials.items():
        for key in ("a", "b", "c", "d"):
            if key not in m:
                raise RuntimeError(f"material {name!r} missing key {key!r}")
            if not isinstance(m[key], (int, float)):
                raise RuntimeError(
                    f"material {name!r} key {key!r} must be numeric, "
                    f"got {type(m[key]).__name__}"
                )
        rng = m.get("valid_range_ghz")
        if (rng is None or not isinstance(rng, list) or len(rng) != 2
                or not (rng[0] < rng[1])):
            raise RuntimeError(
                f"material {name!r} valid_range_ghz must be [low, high] "
                f"with low < high"
            )


def evaluate(
    lib: Dict[str, Any],
    material: str,
    frequency_hz: float,
) -> Dict[str, Any]:
    """Evaluate ``material`` at ``frequency_hz`` and return a result dict.

    Result keys:

    - ``frequency_hz``      \u2014 echoed input
    - ``epsilon_r``         \u2014 real part of relative permittivity
    - ``sigma_s_per_m``     \u2014 conductivity, S/m
    - ``epsilon_r_imag``    \u2014 imaginary part (loss tangent component)
    - ``loss_tangent``      \u2014 ``epsilon_r_imag / epsilon_r``
    - ``in_valid_range``    \u2014 whether ``frequency_hz`` is inside the
                              material's tabulated band
    """
    materials = lib["materials"]
    if material not in materials:
        raise KeyError(
            f"material {material!r} not in library; have "
            f"{sorted(materials.keys())}"
        )
    if frequency_hz <= 0:
        raise ValueError(f"frequency_hz must be > 0, got {frequency_hz}")
    m = materials[material]
    f_ghz = frequency_hz / 1.0e9
    epsilon_r = float(m["a"]) * (f_ghz ** float(m["b"]))
    sigma = float(m["c"]) * (f_ghz ** float(m["d"]))
    eps0 = float(
        lib.get("permittivity_model", {}).get("epsilon_0", _DEFAULT_EPSILON_0)
    )
    epsilon_r_imag = sigma / (2.0 * math.pi * frequency_hz * eps0)
    low, high = m["valid_range_ghz"]
    in_range = (low <= f_ghz <= high)
    if not in_range:
        logger.warning(
            "material %s evaluated at %.3f GHz, outside tabulated range "
            "[%.3f, %.3f] \u2014 results are extrapolated",
            material, f_ghz, low, high,
        )
    return {
        "frequency_hz": frequency_hz,
        "epsilon_r": epsilon_r,
        "sigma_s_per_m": sigma,
        "epsilon_r_imag": epsilon_r_imag,
        "loss_tangent": (epsilon_r_imag / epsilon_r) if epsilon_r > 0 else None,
        "in_valid_range": in_range,
    }


def evaluate_all(
    lib: Dict[str, Any],
    frequencies_hz: Iterable[float],
    materials: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Evaluate every material at every frequency and return a dict::

        {
          material_name: {
            "label": str,
            "p2040_row": Optional[str],
            "evaluations": [ <evaluate() result>, ... ],
          },
          ...
        }
    """
    names = list(materials) if materials else list(lib["materials"].keys())
    out: Dict[str, Any] = {}
    for name in names:
        m = lib["materials"][name]
        out[name] = {
            "label": m.get("label", name),
            "p2040_row": m.get("p2040_row"),
            "evaluations": [
                evaluate(lib, name, float(f)) for f in frequencies_hz
            ],
        }
    return out
