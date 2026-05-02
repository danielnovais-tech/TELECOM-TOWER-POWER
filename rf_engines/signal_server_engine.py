# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Cloud-RF Signal-Server engine adapter.

`Cloud-RF/Signal-Server <https://github.com/Cloud-RF/Signal-Server>`_
is the open-source C++ propagation engine that backs the cloudrf.com
service. We invoke the ``signalserverHD`` binary in *single-link* mode
via stdin/stdout — same wire pattern as :mod:`rf_engines.rf_signals_engine`.

The binary is built from source by ``scripts/build_signal_server.sh``
and produces a single executable. Distribution is GPLv3, so we do not
bundle the binary in the platform image; ops provisions it onto the
ECS task at boot via S3 (same pattern as the ITU digital maps and
MapBiomas raster).

Configure with:

* ``SIGNAL_SERVER_BIN`` — path (default ``/usr/local/bin/signalserverHD``).
* ``SIGNAL_SERVER_TIMEOUT_S`` — wall-clock cap (default 8 s; the C++
  engine is slower than rf-signals on long profiles).
* ``SIGNAL_SERVER_MODEL`` — propagation model id, one of
  ``itm`` (Longley-Rice, default), ``itwom``, ``ericsson``, ``hata``,
  ``cost-hata``, ``sui``, ``fspl``. Mapped onto the binary's ``-pm`` flag.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional, Sequence

from . import register_engine
from .base import LossEstimate, RFEngine

logger = logging.getLogger(__name__)

_BIN_ENV = "SIGNAL_SERVER_BIN"
_DEFAULT_BIN = "/usr/local/bin/signalserverHD"
_TIMEOUT_S = float(os.getenv("SIGNAL_SERVER_TIMEOUT_S", "8.0"))
_MODEL = os.getenv("SIGNAL_SERVER_MODEL", "itm").lower()
_MODEL_FLAGS = {
    "itm": "1", "itwom": "2", "hata": "3", "ericsson": "4",
    "cost-hata": "5", "sui": "6", "fspl": "7",
}


def _resolve_bin() -> Optional[str]:
    explicit = os.getenv(_BIN_ENV) or _DEFAULT_BIN
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    return shutil.which("signalserverHD") or shutil.which("signalserver")


class SignalServerEngine(RFEngine):
    name = "signal-server"

    def is_available(self) -> bool:
        return _resolve_bin() is not None and _MODEL in _MODEL_FLAGS

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

        # Signal-Server's "single link" mode reads a JSON envelope when
        # invoked with --json (custom build flag in our patched fork —
        # see scripts/build_signal_server.sh). The upstream binary
        # expects a parameter file + SDF tiles; we wrap that path with
        # a lightweight harness so the same call site works for both.
        payload = {
            "transmitter": {
                "lat": float(phi_t), "lon": float(lam_t),
                "alt_agl_m": float(htg), "tx_power_dbm": 0.0,
                "antenna_gain_dbi": 0.0,
            },
            "receiver": {
                "lat": float(phi_r), "lon": float(lam_r),
                "alt_agl_m": float(hrg), "antenna_gain_dbi": 0.0,
            },
            "frequency_mhz": float(f_hz) / 1e6,
            "model": _MODEL_FLAGS[_MODEL],
            "polarisation": int(pol or 2),
            "climate_zone": int(zone or 4),
            "reliability_pct": float(time_pct if time_pct is not None else 50.0),
            "location_pct": float(loc_pct if loc_pct is not None else 50.0),
            "terrain_profile_m": h_list,
            "distances_km": d_list,
            "clutter_profile_m": (
                [float(c) for c in clutter_heights_m]
                if clutter_heights_m is not None else []
            ),
            "want": "basic_loss_db",
        }

        with tempfile.NamedTemporaryFile(
            "w+", suffix=".json", delete=True
        ) as tf:
            json.dump(payload, tf)
            tf.flush()
            try:
                proc = subprocess.run(
                    [binary, "--json", tf.name],
                    capture_output=True, timeout=_TIMEOUT_S, check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.warning("signal-server subprocess failed: %s", exc)
                return None

        if proc.returncode != 0:
            logger.warning(
                "signal-server exit=%s stderr=%s",
                proc.returncode, proc.stderr[:512].decode("utf-8", "replace"),
            )
            return None
        try:
            out = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("signal-server returned non-JSON stdout")
            return None

        loss = out.get("basic_loss_db") or out.get("path_loss_db")
        if loss is None:
            return None
        return LossEstimate(
            basic_loss_db=float(loss),
            engine=self.name,
            confidence=0.9,
            extra={"model": _MODEL, "version": out.get("version", "unknown")},
        )


register_engine(SignalServerEngine())
