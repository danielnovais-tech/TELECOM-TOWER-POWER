# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""rf-signals engine adapter \u2014 clean-room subprocess shim.

The companion binary `rfsignals-cli` lives in this repo at
``rf_signals/`` (Rust 2021, stable toolchain). It is a clean-room
implementation of public-domain empirical RF propagation models
(FSPL/ITU-R P.525, Okumura-Hata, COST-231-Hata, ECC-33, Egli,
two-ray plane-earth) \u2014 NO code from the GPL-2.0 ``thebracket/rf-signals``
crate is forked, copied, or linked. The proprietary licence applies
to the in-repo source; the binary itself is invoked over a JSON
stdin/stdout subprocess boundary for parity with the other engines.

Build & install: ``bash scripts/build_rf_signals.sh ~/.local``.
The adapter resolves the binary in this order:

1. ``$RF_SIGNALS_BIN``  (explicit, wins always)
2. ``/usr/local/bin/rfsignals-cli``  (system-wide install)
3. ``shutil.which(\"rfsignals-cli\")``  (anything on ``$PATH``)

Subprocess timeouts and non-zero exits are treated as ``None``
(fail-closed) so the registry simply falls back to the next engine.

Wire schema
-----------
The adapter sends a JSON envelope on stdin and expects a JSON object
on stdout containing ``basic_loss_db`` (float, dB) and optional
``confidence`` / ``model`` / ``version`` fields. See
``rf_signals/src/main.rs`` for the canonical definition.

Environment variables:

* ``RF_SIGNALS_BIN`` \u2014 absolute path to the binary (overrides PATH).
* ``RF_SIGNALS_TIMEOUT_S`` \u2014 subprocess wall-clock cap (default 5 s).
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
            # Schema matches the rfsignals-cli `predict-loss` command
            # (see rf_signals/src/main.rs). Distances in km, heights
            # AGL/AMSL in m, frequency Hz.
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
