# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Sionna RT-backed interference handler for ``/coverage/interference``.

The top-level :mod:`interference_engine` module is the pure-math layer
(ACI mask, linear-domain aggregation, SINR algebra). This adapter sits
between that layer and ``rf_engines.sionna_rt_engine.SionnaRTEngine``,
turning each *aggressor → victim* pair into a per-link path-loss query
and composing the per-pair contribution.

Why a dedicated module instead of inline endpoint code
------------------------------------------------------
* keeps ``telecom_tower_power_api.py`` engine-agnostic — the endpoint
  just dispatches on the resolved engine name and trusts the handler;
* the worker pool (T18 async path) will reuse the same handler from
  ``sqs_lambda_worker`` / ``batch_worker`` without dragging the FastAPI
  request schema along;
* lets us unit-test the Sionna RT branch by stubbing
  :class:`SionnaRTEngine` rather than monkey-patching the endpoint.

Status
------
* **T17.5** — synchronous path. Calls
  :meth:`SionnaRTEngine.predict_basic_loss` once per aggressor inside
  the request lifecycle. Latency budget: ~1-3 s per aggressor on a
  G5.xlarge; the endpoint defaults to ``max_aggressors=200`` so a
  worst-case sweep takes minutes — operators are expected to narrow
  ``search_radius_km`` for sub-second responses.
* **T18 (planned)** — async SQS path. Same handler, called from a GPU
  worker; the HTTP layer becomes "kick off + poll".

Co-channel + ACI
----------------
The handler does **not** re-implement the spectral mask — it composes
:func:`interference_engine.build_contribution` so the same
3GPP-/M.2101-inspired mask used by the FSPL path is applied verbatim.
That guarantees A/B comparisons (FSPL vs Sionna RT) only differ in
the *propagation* term.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from interference_engine import (  # top-level pure-math module
    InterferenceContribution,
    build_contribution,
)

from .base import EngineUnavailable
from .sionna_rt_engine import SionnaRTEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Aggressor:
    """Minimal aggressor record consumed by the handler.

    Decoupled from ``models.Tower`` so the handler stays importable in
    contexts (worker, test) where the SQLAlchemy model is heavier than
    we want to drag in.
    """

    aggressor_id: str
    lat: float
    lon: float
    height_m: float
    f_hz: float
    bw_hz: float
    eirp_dbm: float
    plmn: Optional[str] = None
    n_tx_antennas: int = 1


@dataclass(frozen=True)
class _Victim:
    lat: float
    lon: float
    height_m: float
    f_hz: float
    bw_hz: float
    rx_gain_dbi: float
    plmn: Optional[str] = None
    n_rx_antennas: int = 1


@dataclass(frozen=True)
class HandlerResult:
    """Return shape of :func:`compute_sionna_rt_contributions`."""

    contributions: List[InterferenceContribution]
    n_path_loss_failures: int
    """Aggressors where ``predict_basic_loss`` returned ``None`` — typically
    receiver outside the scene bbox or ray solver hit a degenerate geometry.
    Surfaced in the endpoint response as a diagnostic field."""

    runtime_ms: float
    n_filtered_by_plmn: int = 0


class SionnaRTInterferenceHandler:
    """Glue between :class:`SionnaRTEngine` and the interference math.

    Construct lazily — :meth:`SionnaRTEngine.is_available` is cheap but
    not free (filesystem stat + import probe). The endpoint instantiates
    one handler per request.
    """

    def __init__(self, engine: Optional[SionnaRTEngine] = None) -> None:
        self._engine = engine if engine is not None else SionnaRTEngine()

    def is_available(self) -> bool:
        """Cheap probe — mirrors the ``/coverage/engines/available`` path."""
        return bool(self._engine.is_available())

    def _path_loss_db(
        self,
        *,
        victim: _Victim,
        agg: _Aggressor,
    ) -> Optional[float]:
        """One ray-traced path-loss query.

        ``predict_basic_loss`` requires per-link arrays (``d_km``, ``h_m``)
        for the ITU/legacy interface; the Sionna RT implementation
        ignores those and derives geometry from the scene file, so we
        pass two-element placeholders solely to satisfy the contract.

        ``num_tx_ant`` / ``num_rx_ant`` propagate the MIMO array size
        into the Sionna ``PlanarArray`` config (T20). The H-matrix
        Frobenius norm naturally bakes the MIMO array gain into the
        returned ``basic_loss_db``, so the FSPL-style diversity offset
        must NOT also be applied at the handler level.

        Returns ``None`` if the engine refused (RX outside scene bbox,
        ray solver crash). Logged at WARNING and bubbled up so the
        caller can decrement ``n_contributing``.
        """
        try:
            est = self._engine.predict_basic_loss(
                f_hz=agg.f_hz,
                d_km=(0.0, 1.0),  # placeholder; engine derives from scene
                h_m=(0.0, 0.0),
                htg=agg.height_m,
                hrg=victim.height_m,
                phi_t=agg.lat,
                lam_t=agg.lon,
                phi_r=victim.lat,
                lam_r=victim.lon,
                num_tx_ant=agg.n_tx_antennas,
                num_rx_ant=victim.n_rx_antennas,
            )
        except EngineUnavailable:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "sionna-rt path-loss raised for aggressor=%s; treating as missing",
                agg.aggressor_id,
            )
            return None
        if est is None:
            return None
        return float(est.basic_loss_db)

    def compute_contributions(
        self,
        *,
        victim: _Victim,
        aggressors: Sequence[_Aggressor],
        include_aci: bool = True,
        aci_floor_db: Optional[float] = None,
        aggressor_plmn: Optional[str] = None,
    ) -> HandlerResult:
        """Run Sionna RT for every aggressor and build the contributions.

        Raises :class:`EngineUnavailable` if the underlying engine is not
        ready (``$SIONNA_RT_DISABLED=1``, scene missing, GPU stack absent).
        The endpoint surfaces this as HTTP 503 — the request was
        understood but the back-end isn't online.

        T20 — ``aggressor_plmn`` is a glob applied before tracing each
        aggressor (e.g. ``"724*"`` skips non-Brazilian PLMNs). Skipped
        aggressors do **not** count as path-loss failures.
        """
        if not self.is_available():
            raise EngineUnavailable(
                "sionna-rt engine unavailable: check SIONNA_RT_DISABLED, "
                "SIONNA_RT_SCENE_PATH, mitsuba/sionna_rt imports"
            )

        from interference_engine import plmn_matches  # local import: avoid cycle

        t0 = time.perf_counter()
        contribs: List[InterferenceContribution] = []
        n_fail = 0
        n_filtered_plmn = 0
        for agg in aggressors:
            if not plmn_matches(agg.plmn, aggressor_plmn):
                n_filtered_plmn += 1
                continue
            pl = self._path_loss_db(victim=victim, agg=agg)
            if pl is None:
                n_fail += 1
                continue
            contribs.append(build_contribution(
                aggressor_id=agg.aggressor_id,
                distance_km=_haversine_km(
                    victim.lat, victim.lon, agg.lat, agg.lon,
                ),
                aggressor_f_hz=agg.f_hz,
                aggressor_bw_hz=agg.bw_hz,
                aggressor_eirp_dbm=agg.eirp_dbm,
                victim_f_hz=victim.f_hz,
                victim_bw_hz=victim.bw_hz,
                rx_gain_dbi=victim.rx_gain_dbi,
                path_loss_db=pl,
                include_aci=include_aci,
                aci_floor_db=aci_floor_db,
                plmn=agg.plmn,
                # Sionna RT bakes MIMO gain into pl directly; no offset.
                mimo_gain_db=0.0,
            ))
        runtime_ms = (time.perf_counter() - t0) * 1000.0
        return HandlerResult(
            contributions=contribs,
            n_path_loss_failures=n_fail,
            runtime_ms=runtime_ms,
            n_filtered_by_plmn=n_filtered_plmn,
        )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Local copy to avoid pulling in the platform module at import time."""
    import math as _m

    R = 6371.0088
    p1, p2 = _m.radians(lat1), _m.radians(lat2)
    dphi = _m.radians(lat2 - lat1)
    dlam = _m.radians(lon2 - lon1)
    a = _m.sin(dphi / 2) ** 2 + _m.cos(p1) * _m.cos(p2) * _m.sin(dlam / 2) ** 2
    return float(2 * R * _m.asin(min(1.0, _m.sqrt(a))))


def _aggressor_from_tower(tower, *, default_bw_hz: float, tx_gain_dbi: float) -> _Aggressor:
    """Adapter for ``models.Tower``.

    Lives here so the endpoint stays free of Sionna RT-specific glue.
    """
    return _Aggressor(
        aggressor_id=str(tower.id),
        lat=float(tower.lat),
        lon=float(tower.lon),
        height_m=float(getattr(tower, "height_m", 0.0) or 0.0),
        f_hz=float(tower.primary_freq_hz()),
        bw_hz=default_bw_hz,
        eirp_dbm=float(tower.power_dbm) + tx_gain_dbi,
        plmn=getattr(tower, "plmn", None) or None,
        n_tx_antennas=int(getattr(tower, "n_tx_antennas", 1) or 1),
    )


__all__ = [
    "HandlerResult",
    "SionnaRTInterferenceHandler",
    "_Aggressor",
    "_Victim",
    "_aggressor_from_tower",
    "_haversine_km",
]
