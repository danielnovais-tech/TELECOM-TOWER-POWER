# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""itmlogic (Longley-Rice / ITM v1.2.2) engine adapter.

`edwardoughton/itmlogic <https://github.com/edwardoughton/itmlogic>`_
is a Python implementation of the NTIA Irregular Terrain Model.
Published in JOSS (Oughton et al., 2020). NTIA's ITM has a
permissive disclaimer (no warranty, free to copy/modify/redistribute);
the Python wrapper carries an MIT-style licence — both compatible
with TTP's proprietary container, **no GPL contamination**.

Why we add this alongside ITU-R P.1812
--------------------------------------
* ITM is the *de facto* WISP / FCC propagation model. Operators in
  Brazil rural deployments (DF, Cerrado, Pantanal) often quote ITM
  numbers from Atoll/SPLAT! — comparing TTP predictions side-by-side
  shortens onboarding for these accounts.
* ITM and P.1812 disagree most on long-distance over-rough-terrain
  links — exactly the Brazilian rural use case. The ``/coverage/engines/compare``
  endpoint surfaces this delta automatically.

API surface
-----------
``itmlogic`` does **not** export a single ``itmlogic_p2p`` callable
(despite what some third-party tutorials claim). The package only
exposes the low-level routines (``qlrpfl``, ``avar``, ``qerfi``);
the orchestration lives in upstream's ``scripts/p2p.py`` and we
replicate it here so the engine is self-contained — no scripts/,
no configparser, no shapefile I/O on the request path.

Configuration:

* ``ITMLOGIC_DISABLED`` — set to ``1`` to short-circuit the adapter.
* ``ITMLOGIC_CLIMATE`` — climate code 1-7 (default ``5`` = continental
  temperate, matches upstream sample). Brazil-specific guidance:
  Amazon basin → 1 (equatorial); Cerrado / DF → 2 (continental
  subtropical); Northeast coast → 3 (maritime subtropical).
* ``ITMLOGIC_NS0`` — surface refractivity (N-units), default 314.
* ``ITMLOGIC_EPS`` / ``ITMLOGIC_SGM`` — terrain permittivity / conductivity
  (defaults 15 / 0.005, average ground).
"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)

_DISABLED = os.getenv("ITMLOGIC_DISABLED", "").lower() in {"1", "true", "yes"}
_CLIMATE = int(os.getenv("ITMLOGIC_CLIMATE", "5"))
_NS0 = float(os.getenv("ITMLOGIC_NS0", "314"))
_EPS = float(os.getenv("ITMLOGIC_EPS", "15"))
_SGM = float(os.getenv("ITMLOGIC_SGM", "0.005"))


def _try_import():
    """Lazy import of itmlogic + numpy. Returns the trio used by p2p."""
    try:
        import numpy as np  # type: ignore[import-untyped]
        from itmlogic.misc.qerfi import qerfi  # type: ignore[import-not-found]
        from itmlogic.preparatory_subroutines.qlrpfl import qlrpfl  # type: ignore[import-not-found]
        from itmlogic.statistics.avar import avar  # type: ignore[import-not-found]
    except Exception:
        return None
    return np, qerfi, qlrpfl, avar


class ItmlogicEngine(RFEngine):
    """ITM v1.2.2 wrapped from upstream's qlrpfl + avar.

    The implementation mirrors ``scripts/p2p.py`` from the upstream
    repo. We use the **median** prediction (50 % time, 50 % location,
    50 % confidence) as the basic transmission loss — same convention
    as the platform's ITU-R P.1812 path. Callers that need a different
    quantile (e.g. 90 % reliability for engineering margins) should
    use the explicit ``predict_quantile`` helper.
    """

    name = "itmlogic"

    def is_available(self) -> bool:
        if _DISABLED:
            return False
        return _try_import() is not None

    # ------------------------------------------------------------------
    # Internal: ITM core call
    # ------------------------------------------------------------------

    def _run(
        self,
        np,
        qerfi,
        qlrpfl,
        avar,
        *,
        f_mhz: float,
        distance_km: float,
        terrain_m: Sequence[float],
        tx_h_agl: float,
        rx_h_agl: float,
        pol: int,
        time_pct: float,
        loc_pct: float,
        conf_pct: float = 50.0,
    ) -> Optional[float]:
        """Return ITM basic transmission loss (dB) at the requested
        time/location/confidence quantile, or ``None`` on failure.
        """
        n_pts = len(terrain_m)
        if n_pts < 2 or distance_km <= 0:
            return None

        prop = {
            # Environmental
            "eps": _EPS,
            "sgm": _SGM,
            "klim": _CLIMATE,
            "ens0": _NS0,
            # Polarisation: itmlogic uses 0=horizontal, 1=vertical
            "ipol": 1 if pol == 2 else 0,
            "fmhz": float(f_mhz),
            "d": float(distance_km),
            "hg": [float(tx_h_agl), float(rx_h_agl)],
            # Initial AVAR control flags (see upstream comments)
            "lvar": 5,
            "gma": 157e-9,
            "klimx": 0,
            "mdvarx": 11,
            "kwx": 0,
            "wn": float(f_mhz) / 47.7,
            "ens": _NS0,
        }
        prop["gme"] = prop["gma"] * (1 - 0.04665 * math.exp(prop["ens"] / 179.3))

        # Surface impedance (replicates upstream lines 122-128).
        zq = complex(prop["eps"], 376.62 * prop["sgm"] / prop["wn"])
        zgnd = (zq - 1) ** 0.5
        if prop["ipol"] != 0:
            zgnd = zgnd / zq
        prop["zgnd"] = zgnd

        # Profile vector: [n-1, dx_m, h0, h1, ..., h_{n-1}]
        pfl = [n_pts - 1, 0.0]
        for h in terrain_m:
            pfl.append(float(h))
        pfl[1] = (distance_km * 1000.0) / pfl[0]
        prop["pfl"] = pfl

        # Convert quantile percentages into standard normal arguments
        zr = qerfi([time_pct / 100.0])
        zc = qerfi([conf_pct / 100.0])

        try:
            prop = qlrpfl(prop)
            # Free-space loss (db conversion factor 8.685890 = 10/ln10)
            fs = 8.685890 * math.log(2 * prop["wn"] * prop["dist"])
            avar1, _ = avar(zr[0], 0, zc[0], prop)
        except Exception:
            logger.debug("itmlogic core call failed", exc_info=True)
            return None

        # qlrpfl can set kwx (warning flag) >0 for out-of-domain inputs.
        # 1-3 = informational; 4 = invalid. We only reject 4.
        if prop.get("kwx", 0) >= 4:
            return None
        return float(fs + avar1)

    # ------------------------------------------------------------------
    # Public registry contract
    # ------------------------------------------------------------------

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
        deps = _try_import()
        if deps is None:
            return None
        np, qerfi, qlrpfl, avar = deps

        # ITM is defined for 20 MHz - 20 GHz.
        f_mhz = f_hz / 1e6
        if not (20.0 <= f_mhz <= 20000.0):
            return None
        if len(d_km) != len(h_m) or len(d_km) < 2:
            return None
        if d_km[-1] - d_km[0] <= 0.05:
            return None

        loss = self._run(
            np, qerfi, qlrpfl, avar,
            f_mhz=f_mhz,
            distance_km=float(d_km[-1] - d_km[0]),
            terrain_m=list(h_m),
            tx_h_agl=float(htg),
            rx_h_agl=float(hrg),
            pol=int(pol or 2),
            time_pct=float(time_pct if time_pct is not None else 50.0),
            loc_pct=float(loc_pct if loc_pct is not None else 50.0),
        )
        if loss is None:
            return None
        return LossEstimate(
            basic_loss_db=loss,
            engine=self.name,
            confidence=0.95,  # ITM is deterministic; 0.95 reflects domain trust
            extra={
                "model": "itm-1.2.2",
                "climate": _CLIMATE,
                "ns0": _NS0,
            },
        )


register_engine(ItmlogicEngine())
