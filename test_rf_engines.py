# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Unit tests for the rf_engines registry and compare layer.

We do NOT exercise the third-party engines (rf-signals, signal-server,
sionna) here — those require external binaries / GPU and are
exercised by the nightly coverage-diff workflow. These tests focus
on the contracts:

* registry holds engines by name;
* compare() returns one row per engine, with deltas vs. reference;
* engines that are unavailable yield ``available=False`` rows,
  ``runtime_ms=None``, and don't break the comparison;
* unknown reference name does not raise.
"""
from __future__ import annotations

from typing import Optional, Sequence

import pytest

from rf_engines import register_engine
from rf_engines.base import LossEstimate, RFEngine
from rf_engines.compare import compare


class _StubEngine(RFEngine):
    def __init__(self, name: str, loss_db: Optional[float], available: bool = True):
        self.name = name
        self._loss = loss_db
        self._avail = available

    def is_available(self) -> bool:
        return self._avail

    def predict_basic_loss(self, **_kwargs) -> Optional[LossEstimate]:
        if self._loss is None:
            return None
        return LossEstimate(
            basic_loss_db=self._loss, engine=self.name, confidence=0.5,
        )


@pytest.fixture
def stub_engines() -> list[str]:
    register_engine(_StubEngine("stub-ref", 100.0))
    register_engine(_StubEngine("stub-pessimistic", 115.0))
    register_engine(_StubEngine("stub-down", None, available=False))
    return ["stub-ref", "stub-pessimistic", "stub-down"]


_LINK = dict(
    f_hz=850e6,
    d_km=[0.0, 1.0, 2.0],
    h_m=[700.0, 705.0, 710.0],
    htg=30.0, hrg=1.5,
    phi_t=-23.5, lam_t=-46.6,
    phi_r=-23.6, lam_r=-46.7,
)


def test_compare_returns_one_row_per_engine(stub_engines):
    res = compare(engine_names=stub_engines, reference="stub-ref", **_LINK)
    names = {r.engine for r in res.rows}
    assert names == set(stub_engines)


def test_compare_deltas_against_reference(stub_engines):
    res = compare(engine_names=stub_engines, reference="stub-ref", **_LINK)
    by_name = {r.engine: r for r in res.rows}
    assert by_name["stub-ref"].delta_db == pytest.approx(0.0)
    assert by_name["stub-pessimistic"].delta_db == pytest.approx(15.0)
    # Unavailable engine: no loss, no delta, but still listed.
    assert by_name["stub-down"].available is False
    assert by_name["stub-down"].basic_loss_db is None
    assert by_name["stub-down"].delta_db is None


def test_compare_reference_first_in_rows(stub_engines):
    res = compare(engine_names=stub_engines, reference="stub-ref", **_LINK)
    assert res.rows[0].engine == "stub-ref"


def test_compare_unknown_reference_does_not_raise(stub_engines):
    res = compare(engine_names=stub_engines, reference="does-not-exist", **_LINK)
    # All rows still present; no delta because reference loss is unknown.
    assert all(r.delta_db is None for r in res.rows)


def test_compare_runtime_ms_populated_for_available_engines(stub_engines):
    res = compare(engine_names=stub_engines, reference="stub-ref", **_LINK)
    by_name = {r.engine: r for r in res.rows}
    assert by_name["stub-ref"].runtime_ms is not None
    assert by_name["stub-ref"].runtime_ms >= 0.0
    assert by_name["stub-down"].runtime_ms is None
