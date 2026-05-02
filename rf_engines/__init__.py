# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
rf_engines
==========

Pluggable adapter layer for third-party RF propagation engines.

Each engine is a thin wrapper that:

* exposes a uniform :class:`~rf_engines.base.RFEngine` interface
  (``name``, ``is_available()``, ``predict_basic_loss(...)``,
  ``predict_coverage_grid(...)``);
* is *optional* — if the underlying binary / package / model is not
  installed, ``is_available()`` returns ``False`` and the platform's
  built-in physics + ridge model is used instead;
* is registered via :func:`register_engine` and discoverable through
  :func:`get_engine` / :func:`list_engines`.

The router in ``rf_engines_router.py`` exposes them under
``/coverage/engines/...`` and an A/B compare endpoint at
``/coverage/engines/compare`` that runs N engines on the same input
and returns dB deltas — used both at runtime (operator-driven) and
nightly by the ``coverage-diff`` GitHub Actions robot.

Why a registry instead of inline ``if/elif`` branches?
------------------------------------------------------
The platform already ships three predictors (ridge, SageMaker, ITU-R
P.1812) gated by env vars. Adding rf-signals, Cloud-RF Signal-Server,
and Sionna inline would push ``predict_signal`` past the readability
cliff. The registry keeps ``coverage_predict`` single-purpose and lets
each engine fail closed independently.
"""
from __future__ import annotations

from typing import Dict, List

from .base import EngineUnavailable, LossEstimate, RFEngine

_REGISTRY: Dict[str, RFEngine] = {}


def register_engine(engine: RFEngine) -> None:
    """Register an engine instance under ``engine.name``."""
    if engine.name in _REGISTRY:
        # Re-registration is allowed (test isolation); last writer wins.
        pass
    _REGISTRY[engine.name] = engine


def get_engine(name: str) -> RFEngine:
    """Return the engine registered under ``name`` or raise ``KeyError``."""
    return _REGISTRY[name]


def list_engines(*, available_only: bool = False) -> List[RFEngine]:
    """Return all registered engines, optionally filtered by availability."""
    engines = list(_REGISTRY.values())
    if available_only:
        engines = [e for e in engines if e.is_available()]
    return engines


def _autoregister() -> None:
    """Best-effort import of bundled adapters. Failures are silent —
    the platform must keep booting even if a third-party package is
    missing or its build artefact has not been provisioned yet."""
    import logging
    log = logging.getLogger(__name__)
    for mod in (
        "rf_engines.itu_p1812_engine",
        "rf_engines.itmlogic_engine",
        "rf_engines.rf_signals_engine",
        "rf_engines.signal_server_engine",
        "rf_engines.sionna_engine",
    ):
        try:
            __import__(mod)
        except Exception:  # pragma: no cover — import-time defensive
            log.debug("rf_engines: %s failed to import", mod, exc_info=True)


_autoregister()

__all__ = [
    "EngineUnavailable",
    "LossEstimate",
    "RFEngine",
    "get_engine",
    "list_engines",
    "register_engine",
]
