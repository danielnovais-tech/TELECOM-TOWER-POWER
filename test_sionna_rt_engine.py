# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the Sionna RT 2.x roadmap-scaffold engine.

The engine is intentionally unavailable until the Q2/2026 GPU runtime
lands. These tests pin the contract so a future "fix" that flips it
on prematurely (or removes the registration entirely) trips CI.
"""
from __future__ import annotations

import pytest

import rf_engines  # noqa: F401  — triggers autoregister
from rf_engines import get_engine, list_engines
from rf_engines.sionna_rt_engine import SionnaRTEngine


def test_sionna_rt_is_registered():
    eng = get_engine("sionna-rt")
    assert isinstance(eng, SionnaRTEngine)
    assert eng.name == "sionna-rt"


def test_sionna_rt_appears_in_listing():
    names = [e.name for e in list_engines()]
    assert "sionna-rt" in names


def test_sionna_rt_unavailable_by_default(monkeypatch):
    # Even with the disable flag explicitly cleared, scaffold must
    # report unavailable until the GPU runtime is implemented.
    monkeypatch.delenv("SIONNA_RT_DISABLED", raising=False)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", "")
    eng = SionnaRTEngine()
    assert eng.is_available() is False


def test_sionna_rt_disabled_when_flag_set(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_DISABLED", "1")
    eng = SionnaRTEngine()
    assert eng.is_available() is False


@pytest.mark.parametrize("flag", ["1", "true", "yes", "TRUE", "Yes"])
def test_sionna_rt_disable_flag_truthy_values(monkeypatch, flag):
    monkeypatch.setenv("SIONNA_RT_DISABLED", flag)
    assert SionnaRTEngine().is_available() is False


def test_sionna_rt_predict_returns_none():
    eng = SionnaRTEngine()
    out = eng.predict_basic_loss(
        f_hz=28e9,
        d_km=[0.0, 0.5, 1.0],
        h_m=[10.0, 12.0, 14.0],
        htg=20.0, hrg=1.5,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.51, lam_r=-46.61,
    )
    assert out is None
