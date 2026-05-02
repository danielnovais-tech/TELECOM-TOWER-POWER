# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Signal-Server engine adapter.

Bridges the GPL-2.0 ``Signal-Server`` C++ engine into the rf_engines
registry. Upstream ``Cloud-RF/Signal-Server`` was deleted in 2023; we
build from `W3AXL/Signal-Server <https://github.com/W3AXL/Signal-Server>`_
(active fork, last updated 2025) via :mod:`scripts.build_signal_server`.

WIRE FORMAT
-----------
Upstream's CLI is flag-based and writes a text site-report keyed off
``-o basename``. To make it usable as a request-time backend we apply
``scripts/signal_server_json.patch`` which adds a single ``-json``
flag: in PPA mode the binary then prints one extra JSON line to
stdout::

    {"basic_loss_db": 132.4, "free_space_loss_db": 110.1,
     "distance": 12.345, "frequency_mhz": 900.0,
     "model": 1, "engine": "signal-server"}

Without ``-json`` the binary is byte-for-byte upstream behaviour.
The patch is GPL-2.0-or-later (see its header) and is applied at
build time only — we never bundle the resulting binary into the
container image.

CAVEATS
~~~~~~~
* The adapter cannot pass an arbitrary terrain profile — Signal-Server
  reads its own SRTM .sdf tiles via ``-sdf <dir>``. The ``d_km`` /
  ``h_m`` arguments from :class:`RFEngine` are therefore *ignored*
  here; provide them to ITM/P.1812 if you need profile-based fidelity.
* PPA mode requires a writable ``-o <basename>`` directory because
  PathReport still emits the ``.txt`` site-report alongside the JSON
  line.
* Operator must opt in via ``SIGNAL_SERVER_JSON_FORK=1`` so the
  registry only enables this adapter when a patched binary is actually
  installed.

ENV VARS
~~~~~~~~
* ``SIGNAL_SERVER_BIN`` — path (default ``/usr/local/bin/signalserverHD``)
* ``SIGNAL_SERVER_SDF_DIR`` — path to .sdf tiles (required for any
  realistic loss; without it Signal-Server assumes flat sea-level).
* ``SIGNAL_SERVER_TIMEOUT_S`` — wall-clock cap (default ``8.0``)
* ``SIGNAL_SERVER_MODEL`` — ``itm`` (default), ``itwom``, ``hata``,
  ``ericsson``, ``cost-hata``, ``sui``, ``fspl``
* ``SIGNAL_SERVER_JSON_FORK`` — set to ``1`` to assert that the
  installed binary was built with ``signal_server_json.patch`` applied.
"""
from __future__ import annotations

import json
import logging
import os
import re
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
_SDF_DIR = os.getenv("SIGNAL_SERVER_SDF_DIR", "")
_JSON_FORK = os.getenv("SIGNAL_SERVER_JSON_FORK", "").lower() in {"1", "true", "yes"}

# Mapping to the binary's `-pm` integer. Must match the order documented
# in W3AXL/Signal-Server upstream README.
_MODEL_FLAGS = {
    "itm": "1", "itwom": "2", "hata": "3", "ericsson": "4",
    "cost-hata": "5", "sui": "6", "fspl": "7",
}

# Match a JSON object on a single line of stdout that contains
# "basic_loss_db". Tolerant of additional log lines from spdlog around it.
_JSON_LINE_RE = re.compile(rb'^\s*\{[^\n]*"basic_loss_db"[^\n]*\}\s*$')


def _resolve_bin() -> Optional[str]:
    explicit = os.getenv(_BIN_ENV) or _DEFAULT_BIN
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    return shutil.which("signalserverHD") or shutil.which("signalserver")


def _extract_json_line(stdout: bytes) -> Optional[dict]:
    """Find the JSON line emitted by signal_server_json.patch.

    Signal-Server prints various spdlog progress lines; our patch
    emits a single object on its own line. Scan from the end (last
    match wins, which is what PathReport's emit_json branch produces).
    """
    for raw in reversed(stdout.splitlines()):
        if _JSON_LINE_RE.match(raw):
            try:
                return json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
    return None


class SignalServerEngine(RFEngine):
    name = "signal-server"

    def is_available(self) -> bool:
        # Triple gate: opt-in env var + binary on disk + known model.
        # Default upstream binaries fail the JSON_FORK assertion; only
        # ones built via scripts/build_signal_server.sh (which applies
        # signal_server_json.patch) should set the env var.
        return (
            _JSON_FORK
            and _resolve_bin() is not None
            and _MODEL in _MODEL_FLAGS
        )

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
        del d_km, h_m, clutter_heights_m, pol, zone, time_pct, loc_pct
        binary = _resolve_bin()
        if binary is None:
            return None
        model_flag = _MODEL_FLAGS.get(_MODEL)
        if model_flag is None:
            return None

        with tempfile.TemporaryDirectory(prefix="ss-") as workdir:
            basename = os.path.join(workdir, "link")
            argv = [
                binary,
                "-lat", f"{float(phi_t):.6f}",
                "-lon", f"{float(lam_t):.6f}",
                "-rla", f"{float(phi_r):.6f}",
                "-rlo", f"{float(lam_r):.6f}",
                "-txh", f"{float(htg):.2f}",
                "-rxh", f"{float(hrg):.2f}",
                "-f", f"{float(f_hz) / 1e6:.3f}",
                "-erp", "0",
                "-pm", model_flag,
                "-m",
                "-o", basename,
                "-json",
            ]
            if _SDF_DIR:
                argv.extend(["-sdf", _SDF_DIR])

            try:
                proc = subprocess.run(
                    argv, capture_output=True,
                    timeout=_TIMEOUT_S, check=False,
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

        out = _extract_json_line(proc.stdout)
        if out is None:
            logger.warning(
                "signal-server stdout did not contain JSON loss line; "
                "is the binary built with signal_server_json.patch?"
            )
            return None

        loss = out.get("basic_loss_db")
        if loss is None:
            return None
        return LossEstimate(
            basic_loss_db=float(loss),
            engine=self.name,
            confidence=0.9,
            extra={
                "model": _MODEL,
                "free_space_loss_db": out.get("free_space_loss_db"),
                "distance": out.get("distance"),
            },
        )


register_engine(SignalServerEngine())
