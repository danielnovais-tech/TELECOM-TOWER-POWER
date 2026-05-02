# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Abstract base for RF propagation engines.

All engines speak in **basic transmission loss (dB)** so the registry
results can be compared apples-to-apples. Conversion to received
signal strength (dBm) is the caller's responsibility — that requires
EIRP and rx gain knowledge that varies per request.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional, Sequence


class EngineUnavailable(RuntimeError):
    """Raised when an engine's binary / model / package is missing.

    Engines should *never* raise this from ``predict_basic_loss`` — they
    must instead return ``None``. The exception type exists for the
    rare cases (e.g. health probes) where the caller wants an explicit
    failure.
    """


@dataclass(frozen=True)
class LossEstimate:
    """Output of :meth:`RFEngine.predict_basic_loss`."""

    basic_loss_db: float
    """Basic transmission loss Lb in dB."""

    engine: str
    """Identifier of the engine that produced this estimate."""

    confidence: float = 0.5
    """Engine self-reported confidence in [0, 1]. ITU/physics models
    use 1.0 (deterministic). ML models report calibrated values."""

    runtime_ms: Optional[float] = None
    """Wall time of the prediction call, populated by the registry
    layer when the call is dispatched through it."""

    extra: dict = field(default_factory=dict)
    """Engine-specific metadata (model version, CLI args, etc.).
    Surfaced verbatim by ``/coverage/engines/compare``."""


class RFEngine(abc.ABC):
    """Adapter contract.

    Implementations must be cheap to instantiate. Heavy resources
    (binary subprocess, GPU model) are lazily acquired inside
    :meth:`is_available` / :meth:`predict_basic_loss`.
    """

    name: str

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return ``True`` iff the engine can serve a prediction now.

        Must be cheap (< ~50 ms): the registry calls this on every
        compare invocation.
        """

    @abc.abstractmethod
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
        """Predict basic transmission loss for one Tx→Rx link.

        The signature mirrors :func:`itu_p1812.predict_basic_loss` so
        callers can swap engines without rewriting feature plumbing.

        Returns ``None`` on any failure (missing dependency, invalid
        domain, subprocess timeout) — engines must fail closed.
        """
