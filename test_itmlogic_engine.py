# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the itmlogic adapter.

We skip when itmlogic is not installed — CI image may or may not have
it. The fixture is the upstream Crystal Palace → Mursley sample
(Oughton 2020, JOSS), giving a reference loss in the 130-140 dB band.
"""
from __future__ import annotations

import os
import pytest

itmlogic = pytest.importorskip("itmlogic")

from rf_engines import get_engine

# Crystal Palace → Mursley reference sample (upstream scripts/p2p.py).
_PROFILE = [
    96, 84, 65, 46, 46, 46, 61, 41, 33, 27, 23, 19, 15, 15, 15,
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 17, 19, 21, 23,
    25, 27, 29, 35, 46, 41, 35, 30, 33, 35, 37, 40, 35, 30, 51,
    62, 76, 46, 46, 46, 46, 46, 46, 50, 56, 67, 106, 83, 95, 112,
    137, 137, 76, 103, 122, 122, 83, 71, 61, 64, 67, 71, 74, 77,
    79, 86, 91, 83, 76, 68, 63, 76, 107, 107, 107, 119, 127, 133,
    135, 137, 142, 148, 152, 152, 107, 137, 104, 91, 99, 120, 152,
    152, 137, 168, 168, 122, 137, 137, 170, 183, 183, 187, 194,
    201, 192, 152, 152, 166, 177, 198, 156, 127, 116, 107, 104,
    101, 98, 95, 103, 91, 97, 102, 107, 107, 107, 103, 98, 94,
    91, 105, 122, 122, 122, 122, 122, 137, 137, 137, 137, 137,
    137, 137, 137, 140, 144, 147, 150, 152, 159,
]
_DIST_KM = 77.8


def _d_km():
    n = len(_PROFILE)
    return [_DIST_KM * i / (n - 1) for i in range(n)]


def test_itmlogic_engine_registered_and_available():
    e = get_engine("itmlogic")
    assert e.name == "itmlogic"
    # In CI we may have set ITMLOGIC_DISABLED=1 explicitly to skip;
    # respect that, otherwise expect it to be available.
    if os.getenv("ITMLOGIC_DISABLED"):
        assert not e.is_available()
    else:
        assert e.is_available()


@pytest.mark.skipif(
    os.getenv("ITMLOGIC_DISABLED"),
    reason="itmlogic disabled via env var",
)
def test_itmlogic_predict_basic_loss_reference_sample():
    e = get_engine("itmlogic")
    res = e.predict_basic_loss(
        f_hz=41.5e6,
        d_km=_d_km(),
        h_m=_PROFILE,
        htg=143.9,
        hrg=8.5,
        phi_t=51.42,
        lam_t=-0.075,
        phi_r=51.95,
        lam_r=-0.81,
        pol=1,
        time_pct=50.0,
        loc_pct=50.0,
    )
    assert res is not None
    # Upstream's median quantile lands around 128-138 dB. Anything
    # outside 100-180 dB indicates a wiring regression.
    assert 100.0 < res.basic_loss_db < 180.0
    assert res.engine == "itmlogic"
    assert res.extra["model"] == "itm-1.2.2"


def test_itmlogic_rejects_out_of_band_frequency():
    e = get_engine("itmlogic")
    # 1 MHz is below ITM's valid 20 MHz - 20 GHz band.
    res = e.predict_basic_loss(
        f_hz=1e6,
        d_km=_d_km(),
        h_m=_PROFILE,
        htg=30.0, hrg=2.0,
        phi_t=0.0, lam_t=0.0, phi_r=0.0, lam_r=0.5,
    )
    assert res is None


def test_itmlogic_rejects_mismatched_profile_lengths():
    e = get_engine("itmlogic")
    res = e.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0],  # mismatched
        htg=30.0, hrg=2.0,
        phi_t=0.0, lam_t=0.0, phi_r=0.0, lam_r=0.5,
    )
    assert res is None
