# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for itu_p1812.py — uses a fake Py1812 module to avoid the
heavy upstream install + ITU digital maps in CI."""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

import itu_p1812


@pytest.fixture(autouse=True)
def _reset():
    itu_p1812._reset_for_tests()
    sys.modules.pop("Py1812", None)
    yield
    itu_p1812._reset_for_tests()
    sys.modules.pop("Py1812", None)


def _install_fake_p1812(*, returned_lb: float = 130.5,
                        capture: list | None = None) -> None:
    """Inject a fake ``Py1812.P1812.bt_loss`` into sys.modules."""
    pkg = types.ModuleType("Py1812")
    sub = types.ModuleType("Py1812.P1812")

    def bt_loss(*args, **kwargs):
        if capture is not None:
            capture.append((args, kwargs))
        return returned_lb, 60.0

    sub.bt_loss = bt_loss
    pkg.P1812 = sub  # type: ignore[attr-defined]
    sys.modules["Py1812"] = pkg
    sys.modules["Py1812.P1812"] = sub


def test_is_available_false_when_package_absent():
    assert itu_p1812.is_available() is False


def test_predict_basic_loss_returns_none_without_package():
    out = itu_p1812.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    assert out is None


def test_predict_basic_loss_calls_p1812_when_available():
    captured: list = []
    _install_fake_p1812(returned_lb=128.7, capture=captured)

    lb = itu_p1812.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0, 3.0],
        h_m=[100.0, 110.0, 130.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    assert lb == pytest.approx(128.7)
    assert len(captured) == 1
    args, kwargs = captured[0]
    # 1st positional arg is frequency in GHz, not Hz.
    assert args[0] == pytest.approx(0.9)


def test_out_of_range_freq_returns_none():
    _install_fake_p1812()
    # 10 GHz is above the P.1812 domain (≤ 6 GHz).
    out = itu_p1812.predict_basic_loss(
        f_hz=10e9,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    assert out is None


def test_short_profile_returns_none():
    _install_fake_p1812()
    out = itu_p1812.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 0.05],
        h_m=[100.0, 100.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    assert out is None


def test_lru_cache_dedups_repeated_calls():
    captured: list = []
    _install_fake_p1812(returned_lb=125.0, capture=captured)

    kwargs = dict(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    a = itu_p1812.predict_basic_loss(**kwargs)
    b = itu_p1812.predict_basic_loss(**kwargs)
    c = itu_p1812.predict_basic_loss(**kwargs)
    assert a == b == c == pytest.approx(125.0)
    # P1812.bt_loss should only have been hit once for identical inputs.
    assert len(captured) == 1


def test_p1812_failure_returns_none(monkeypatch):
    pkg = types.ModuleType("Py1812")
    sub = types.ModuleType("Py1812.P1812")

    def bt_loss(*a, **kw):
        raise RuntimeError("synthetic failure")

    sub.bt_loss = bt_loss
    pkg.P1812 = sub  # type: ignore[attr-defined]
    sys.modules["Py1812"] = pkg
    sys.modules["Py1812.P1812"] = sub

    out = itu_p1812.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    )
    assert out is None


def test_disabled_env_short_circuits(monkeypatch):
    monkeypatch.setattr(itu_p1812, "_DISABLED", True)
    _install_fake_p1812()
    assert itu_p1812.is_available() is False
    assert itu_p1812.predict_basic_loss(
        f_hz=900e6,
        d_km=[0.0, 1.0, 2.0],
        h_m=[100.0, 110.0, 120.0],
        htg=30.0, hrg=10.0,
        phi_t=-23.5, lam_t=-46.6,
        phi_r=-23.6, lam_r=-46.5,
    ) is None
