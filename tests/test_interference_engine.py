# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for ``interference_engine`` — pure helpers.

End-to-end coverage of the spectral mask, linear-domain aggregation,
thermal noise and SINR algebra. Endpoint wiring is exercised in
``test_interference_endpoint.py``; this file stays I/O-free so it
runs in <1 ms in CI.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from interference_engine import (  # noqa: E402
    InterferenceContribution,
    aci_attenuation_db,
    aggregate_interference_dbm,
    build_contribution,
    i_over_n_db,
    sinr_db,
    thermal_noise_dbm,
    top_n_contributions,
)


# ── ACI mask ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "victim_f, agg_f, bw, expected",
    [
        # Co-channel: |Δ|/BW = 0          → 0 dB
        (2_600e6, 2_600e6, 20e6,  0.0),
        # |Δ|/BW = 0.4 (still co-channel) → 0 dB
        (2_600e6, 2_608e6, 20e6,  0.0),
        # |Δ|/BW = 1.0 (1st adjacent)     → 30 dB
        (2_600e6, 2_620e6, 20e6, 30.0),
        # |Δ|/BW = 2.0 (2nd adjacent)     → 43 dB
        (2_600e6, 2_640e6, 20e6, 43.0),
        # |Δ|/BW = 5.0 (far out)          → 60 dB floor
        (2_600e6, 2_700e6, 20e6, 60.0),
    ],
)
def test_aci_mask_steps(victim_f, agg_f, bw, expected):
    assert aci_attenuation_db(victim_f, bw, agg_f, bw) == expected


def test_aci_mask_floor_override():
    # Operator with measured 80 dB filter rejection passes the floor in.
    att = aci_attenuation_db(2_600e6, 20e6, 2_700e6, 20e6, aci_floor_db=80.0)
    assert att == 80.0


def test_aci_mask_uses_max_bw():
    # 5-MHz victim vs 20-MHz aggressor: norm = max(BW) = 20 MHz, so a
    # 12 MHz offset is |Δ|/norm = 0.6 → 1st adjacent (30 dB), not co.
    att = aci_attenuation_db(2_600e6, 5e6, 2_612e6, 20e6)
    assert att == 30.0


def test_aci_mask_rejects_zero_bandwidth():
    with pytest.raises(ValueError):
        aci_attenuation_db(2_600e6, 0, 2_600e6, 20e6)


# ── Thermal noise ───────────────────────────────────────────────

def test_thermal_noise_20mhz_5db_nf():
    # kT = -174 dBm/Hz, 10·log10(20e6) = 73.01, NF = 5 → ~ -95.99 dBm
    n = thermal_noise_dbm(20e6, noise_figure_db=5.0)
    assert n == pytest.approx(-95.99, abs=0.01)


def test_thermal_noise_default_nf_is_5():
    assert thermal_noise_dbm(20e6) == thermal_noise_dbm(20e6, 5.0)


def test_thermal_noise_validates():
    with pytest.raises(ValueError):
        thermal_noise_dbm(0)
    with pytest.raises(ValueError):
        thermal_noise_dbm(20e6, noise_figure_db=-1.0)


# ── Linear-domain aggregation ───────────────────────────────────

def _contrib(rx_dbm: float, ident: str = "x") -> InterferenceContribution:
    return InterferenceContribution(
        aggressor_id=ident, distance_km=1.0,
        aggressor_f_hz=2_600e6, aggressor_bw_hz=20e6,
        eirp_dbm=60.0, path_loss_db=100.0, aci_db=0.0,
        rx_power_dbm=rx_dbm,
    )


def test_aggregate_two_equal_contributors_is_3db_higher():
    # Two -90 dBm aggressors at the same victim → -87 dBm aggregate.
    out = aggregate_interference_dbm([_contrib(-90), _contrib(-90)])
    assert out == pytest.approx(-86.99, abs=0.01)


def test_aggregate_skips_minus_infinity():
    out = aggregate_interference_dbm([
        _contrib(-90), _contrib(float("-inf")), _contrib(-90),
    ])
    assert out == pytest.approx(-86.99, abs=0.01)


def test_aggregate_returns_none_when_all_infinite():
    out = aggregate_interference_dbm([
        _contrib(float("-inf")), _contrib(float("-inf")),
    ])
    assert out is None


def test_aggregate_returns_none_for_empty_iterable():
    assert aggregate_interference_dbm([]) is None


# ── I/N + SINR ──────────────────────────────────────────────────

def test_i_over_n_basic():
    # I = -90 dBm, N = -95 dBm → I/N = +5 dB
    assert i_over_n_db(-90.0, -95.0) == pytest.approx(5.0, abs=1e-9)


def test_i_over_n_passes_through_none():
    assert i_over_n_db(None, -95.0) is None


def test_sinr_no_interference_collapses_to_snr():
    # S = -80, I = None, N = -95 → SINR = SNR = 15 dB
    assert sinr_db(-80.0, None, -95.0) == pytest.approx(15.0, abs=1e-9)


def test_sinr_interference_dominated():
    # S = -80, I = -75 (20 dB above N), N = -95.
    # I_lin >> N_lin → I+N ≈ I = -74.96 → SINR ≈ -5.04 dB
    out = sinr_db(-80.0, -75.0, -95.0)
    assert out == pytest.approx(-5.04, abs=0.05)


def test_sinr_returns_none_without_signal():
    assert sinr_db(None, -75.0, -95.0) is None


# ── build_contribution ──────────────────────────────────────────

def test_build_contribution_co_channel_arithmetic():
    c = build_contribution(
        aggressor_id="t1", distance_km=5.0,
        aggressor_f_hz=2_600e6, aggressor_bw_hz=20e6,
        aggressor_eirp_dbm=60.0,
        victim_f_hz=2_600e6, victim_bw_hz=20e6,
        rx_gain_dbi=12.0, path_loss_db=110.0, include_aci=True,
    )
    # Pr = 60 - 110 + 12 - 0 = -38 dBm
    assert c.rx_power_dbm == pytest.approx(-38.0, abs=1e-6)
    assert c.aci_db == 0.0


def test_build_contribution_adjacent_channel_attenuates_by_aci():
    c = build_contribution(
        aggressor_id="t2", distance_km=5.0,
        aggressor_f_hz=2_620e6, aggressor_bw_hz=20e6,
        aggressor_eirp_dbm=60.0,
        victim_f_hz=2_600e6, victim_bw_hz=20e6,
        rx_gain_dbi=12.0, path_loss_db=110.0, include_aci=True,
    )
    # |Δ|/BW = 1.0 → 30 dB ACI; Pr = 60 - 110 + 12 - 30 = -68 dBm
    assert c.aci_db == 30.0
    assert c.rx_power_dbm == pytest.approx(-68.0, abs=1e-6)


def test_build_contribution_co_channel_only_mutes_adjacent():
    c = build_contribution(
        aggressor_id="t3", distance_km=5.0,
        aggressor_f_hz=2_620e6, aggressor_bw_hz=20e6,
        aggressor_eirp_dbm=60.0,
        victim_f_hz=2_600e6, victim_bw_hz=20e6,
        rx_gain_dbi=12.0, path_loss_db=110.0, include_aci=False,
    )
    assert c.rx_power_dbm == float("-inf")


# ── top_n_contributions ─────────────────────────────────────────

def test_top_n_orders_descending_drops_minus_inf():
    cs = [
        _contrib(-95, "a"), _contrib(-80, "b"),
        _contrib(float("-inf"), "c"), _contrib(-85, "d"),
    ]
    top2 = top_n_contributions(cs, n=2)
    assert [c.aggressor_id for c in top2] == ["b", "d"]


def test_top_n_zero_returns_empty():
    assert top_n_contributions([_contrib(-90)], n=0) == []
