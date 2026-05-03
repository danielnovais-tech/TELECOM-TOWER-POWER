# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Backward-compatible rf-signals alias over the itmlogic engine.

The stack migrated away from the Rust subprocess binary to direct ITM
Python execution via :mod:`rf_engines.itmlogic_engine`.

To avoid breaking existing clients and historical compare reports that
still request ``engine='rf-signals'``, this module keeps the same name
but forwards predictions to ``itmlogic`` internally.
"""
from __future__ import annotations

from . import register_engine
from .itmlogic_engine import ItmlogicEngine


class RfSignalsEngine(ItmlogicEngine):
    name = "rf-signals"


register_engine(RfSignalsEngine())
