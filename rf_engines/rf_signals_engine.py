# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""rf-signals (Rust) engine adapter — PLACEHOLDER.

`thebracket/rf-signals <https://github.com/thebracket/rf-signals>`_ is a
pure-Rust port of the Cloud-RF Signal Server / SPLAT! propagation
algorithms (ITWOM3, HATA, COST/HATA, ECC33, EGLI, FSPL, SUI, Plane
Earth, SOIL).

.. warning::

    The upstream repo has been **unmaintained for ~5 years**, requires
    nightly Rust from 2020 + old Rocket, and is **GPL-2.0** licensed.
    Linking it into a proprietary process would contaminate the
    platform; we therefore use a *subprocess* shim binary
    (``rfsignals-cli``) that the Python side shells out to. The
    binary is **never bundled** in the TTP container image — ops
    builds it from a maintained fork and provisions it via S3.

    No working ``rfsignals-cli`` is shipped with this repo. Reviving
    the engine requires forking upstream, pinning a buildable
    toolchain, and mapping the real ``rf_signal_algorithms::rfcalc``
    functions onto the JSON wire schema below. Until that lands,
    :meth:`is_available` returns ``False`` and the registry simply
    skips this engine — see ``docs/rf-engines.md``.

Wire schema (when revived)
--------------------------
The adapter sends a JSON envelope on stdin and expects a JSON object
on stdout containing ``basic_loss_db`` (float, dB) and optional
``confidence`` / ``model`` / ``version`` fields. Subprocess timeouts
and non-zero exits are treated as ``None`` (fail-closed).

Environment variables:

* ``RF_SIGNALS_BIN`` — absolute path to a shim binary. If unset / not
  executable / not on PATH, the engine reports unavailable.
* ``RF_SIGNALS_TIMEOUT_S`` — subprocess wall-clock cap (default 5 s).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)

_BIN_ENV = "RF_SIGNALS_BIN"
_DEFAULT_BIN = "/usr/local/bin/rfsignals-cli"
_TIMEOUT_S = float(os.getenv("RF_SIGNALS_TIMEOUT_S", "5.0"))


def _resolve_bin() -> Optional[str]:
    explicit = os.getenv(_BIN_ENV) or _DEFAULT_BIN
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    # Fall back to PATH lookup so dev machines work without env var.
    fallback = shutil.which("rfsignals-cli")
    return fallback


class RfSignalsEngine(RFEngine):
    name = "rf-signals"

    def is_available(self) -> bool:
        return _resolve_bin() is not None

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
        binary = _resolve_bin()
        if binary is None:
            return None
        d_list = [float(x) for x in d_km]
        h_list = [float(x) for x in h_m]
        if len(d_list) < 2 or len(d_list) != len(h_list):
            return None

        payload = {
            # Schema mirrors a future rfsignals-cli `predict-loss`
            # subcommand (see docs/rf-engines.md for the revival plan).
            # Distances in km, heights AGL/AMSL in m, freq Hz.
            "command": "predict-loss",
            "frequency_hz": float(f_hz),
            "distances_km": d_list,
            "terrain_m": h_list,
            "tx_height_agl_m": float(htg),
            "rx_height_agl_m": float(hrg),
            "tx_lat": float(phi_t),
            "tx_lon": float(lam_t),
            "rx_lat": float(phi_r),
            "rx_lon": float(lam_r),
            "polarisation": "vertical" if (pol or 2) == 2 else "horizontal",
            "time_pct": float(time_pct if time_pct is not None else 50.0),
            "loc_pct": float(loc_pct if loc_pct is not None else 50.0),
            "clutter_m": (
                [float(c) for c in clutter_heights_m]
                if clutter_heights_m is not None else None
            ),
        }
        try:
            proc = subprocess.run(
                [binary, "--json"],
                input=json.dumps(payload).encode("utf-8"),
                capture_output=True,
                timeout=_TIMEOUT_S,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("rf-signals subprocess failed: %s", exc)
            return None
        if proc.returncode != 0:
            logger.warning(
                "rf-signals exit=%s stderr=%s",
                proc.returncode, proc.stderr[:512].decode("utf-8", "replace"),
            )
            return None
        try:
            out = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("rf-signals returned non-JSON stdout")
            return None
        loss = out.get("basic_loss_db")
        if loss is None:
            return None
        return LossEstimate(
            basic_loss_db=float(loss),
            engine=self.name,
            confidence=float(out.get("confidence", 0.85)),
            extra={
                "model": out.get("model", "itm"),
                "version": out.get("version", "unknown"),
            },
        )


register_engine(RfSignalsEngine())
