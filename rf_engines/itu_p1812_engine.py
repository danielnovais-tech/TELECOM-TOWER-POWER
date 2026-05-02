# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""ITU-R P.1812 engine — wraps the existing :mod:`itu_p1812` module.

This is the *reference* engine in the registry. Other adapters are
benchmarked against it in the A/B compare endpoint and in CI.
"""
from __future__ import annotations

from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

try:
    import itu_p1812 as _p1812
except Exception:  # pragma: no cover
    _p1812 = None  # type: ignore[assignment]


class ItuP1812Engine(RFEngine):
    name = "itu-p1812"

    def is_available(self) -> bool:
        return _p1812 is not None and _p1812.is_available()

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
        if _p1812 is None:
            return None
        lb = _p1812.predict_basic_loss(
            f_hz=f_hz, d_km=d_km, h_m=h_m, htg=htg, hrg=hrg,
            phi_t=phi_t, lam_t=lam_t, phi_r=phi_r, lam_r=lam_r,
            clutter_heights_m=clutter_heights_m, pol=pol, zone=zone,
            time_pct=time_pct, loc_pct=loc_pct,
        )
        if lb is None:
            return None
        return LossEstimate(
            basic_loss_db=float(lb),
            engine=self.name,
            confidence=1.0,
            extra={"recommendation": "ITU-R P.1812-7"},
        )


register_engine(ItuP1812Engine())
