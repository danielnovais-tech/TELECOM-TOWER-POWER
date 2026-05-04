# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Unit tests for the rf_engines registry and compare layer.

We do NOT exercise the third-party engines (itmlogic, signal-server,
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


# ── SionnaRTEngine — T9 feature-flag + predict (T9) ──────────────

import json
import sys
import types

from rf_engines.sionna_rt_engine import SionnaRTEngine, _has_gpu_stack


# ── Availability conditions ───────────────────────────────────────

def test_is_available_false_by_default(monkeypatch):
    """Disabled=1 (default) → always unavailable, even with full scene."""
    monkeypatch.delenv("SIONNA_RT_DISABLED", raising=False)
    assert SionnaRTEngine().is_available() is False


def test_is_available_false_when_disabled_explicit(monkeypatch, tmp_path):
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "1")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))
    assert SionnaRTEngine().is_available() is False


def test_is_available_false_when_scene_path_empty(monkeypatch):
    _fake_gpu_stack(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.delenv("SIONNA_RT_SCENE_PATH", raising=False)
    assert SionnaRTEngine().is_available() is False


def test_is_available_false_when_scene_xml_missing(monkeypatch, tmp_path):
    _fake_gpu_stack(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))
    # manifest exists but no scene.xml
    (tmp_path / "manifest.json").write_text("{}")
    assert SionnaRTEngine().is_available() is False


def test_is_available_false_when_manifest_missing(monkeypatch, tmp_path):
    _fake_gpu_stack(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))
    # scene.xml exists but no manifest
    (tmp_path / "scene.xml").write_text("<scene/>")
    assert SionnaRTEngine().is_available() is False


def test_is_available_false_when_gpu_stack_missing(monkeypatch, tmp_path):
    _make_scene_dir(tmp_path)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))
    # Remove GPU modules if they snuck in
    for m in ("mitsuba", "sionna_rt"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    # Ensure import fails by injecting a broken finder
    import importlib.abc, importlib.machinery

    class _BlockingLoader(importlib.abc.Loader):
        def create_module(self, spec): raise ImportError("blocked by test")
        def exec_module(self, mod): pass

    class _BlockingFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname in ("mitsuba", "sionna_rt"):
                return importlib.machinery.ModuleSpec(fullname, _BlockingLoader())
            return None

    finder = _BlockingFinder()
    sys.meta_path.insert(0, finder)
    try:
        assert SionnaRTEngine().is_available() is False
    finally:
        sys.meta_path.remove(finder)
        for m in ("mitsuba", "sionna_rt"):
            sys.modules.pop(m, None)


def test_is_available_true_when_all_conditions_met(monkeypatch, tmp_path):
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))
    assert SionnaRTEngine().is_available() is True


# ── predict_basic_loss ────────────────────────────────────────────

def test_predict_basic_loss_returns_loss_estimate(monkeypatch, tmp_path):
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_tracer(monkeypatch, loss_db=95.3)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    eng = SionnaRTEngine()
    est = eng.predict_basic_loss(**_LINK)
    assert est is not None
    assert est.engine == "sionna-rt"
    assert est.basic_loss_db == pytest.approx(95.3)
    assert est.confidence == 1.0


def test_predict_basic_loss_job_has_1x1_raster(monkeypatch, tmp_path):
    """The job fired at the tracer must be exactly a 1×1 grid."""
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    captured_jobs: list = []
    _patch_tracer(monkeypatch, loss_db=80.0, capture=captured_jobs)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    SionnaRTEngine().predict_basic_loss(**_LINK)
    assert len(captured_jobs) == 1
    job = captured_jobs[0]
    assert job.rows == 1 and job.cols == 1
    assert job.frequency_hz == _LINK["f_hz"]
    assert job.tx_lat == _LINK["phi_t"]
    assert job.tx_lon == _LINK["lam_t"]
    assert job.tx_height_m == _LINK["htg"]
    # Receiver bbox must straddle phi_r / lam_r
    assert job.bbox_south < _LINK["phi_r"] < job.bbox_north
    assert job.bbox_west  < _LINK["lam_r"] < job.bbox_east


def test_predict_basic_loss_returns_none_on_tracer_exception(monkeypatch, tmp_path):
    """Tracer errors must fail closed (None), not 500."""
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_tracer(monkeypatch, exc=RuntimeError("GPU OOM"))
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    assert SionnaRTEngine().predict_basic_loss(**_LINK) is None


def test_predict_basic_loss_in_compare_loop(monkeypatch, tmp_path):
    """Engine participates normally in compare() when available."""
    from rf_engines.compare import compare

    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_tracer(monkeypatch, loss_db=110.0)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    register_engine(SionnaRTEngine())  # re-register with fresh state
    register_engine(_StubEngine("itu-p1812-stub", 100.0))

    res = compare(
        engine_names=["sionna-rt", "itu-p1812-stub"],
        reference="itu-p1812-stub",
        **_LINK,
    )
    by_name = {r.engine: r for r in res.rows}
    assert by_name["sionna-rt"].available is True
    assert by_name["sionna-rt"].basic_loss_db == pytest.approx(110.0)
    assert by_name["sionna-rt"].delta_db == pytest.approx(10.0)


# ── Helpers ────────────────────────────────────────────────────────

_GOOD_MANIFEST = {
    "schema_version": 1,
    "aoi_name": "sp-centro",
    "bbox": [-23.56, -46.66, -23.54, -46.62],
    "frequencies_hz": [28e9],
    "p2040_table_version": "1",
    "implementation_status": "complete",
}


def _make_scene_dir(d):
    (d / "scene.xml").write_text("<scene/>")
    (d / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))


def _fake_gpu_stack(monkeypatch):
    """Inject minimal mitsuba + sionna_rt stubs into sys.modules."""
    fake_mi = types.SimpleNamespace(
        variants=lambda: ["llvm_ad_rgb", "scalar_rgb"],
        set_variant=lambda _: None,
    )
    monkeypatch.setitem(sys.modules, "mitsuba", fake_mi)

    # sionna_rt needs to be importable via _has_gpu_stack; content
    # doesn't matter here — _patch_tracer replaces the tracer.
    fake_srt = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "sionna_rt", fake_srt)


def _patch_tracer(monkeypatch, *, loss_db=None, exc=None, capture=None):
    """Replace _SionnaRtTracer in sionna_rt_engine's dynamic import path."""
    import numpy as np  # type: ignore[import-not-found]

    class _FakeTracer:
        def __init__(self): pass

        def trace(self, scene_dir, job):
            if capture is not None:
                capture.append(job)
            if exc is not None:
                raise exc
            arr = np.full((job.rows, job.cols), float(loss_db), dtype="float32")
            return arr

    # The engine imports the worker module dynamically; inject a fake
    # that exposes _SionnaRtTracer + the Job dataclass.
    import scripts.sionna_rt_worker as _real_worker
    fake_worker = types.ModuleType("scripts.sionna_rt_worker")
    # Copy everything from the real worker; only replace _SionnaRtTracer.
    fake_worker.__dict__.update(_real_worker.__dict__)
    fake_worker._SionnaRtTracer = _FakeTracer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "scripts.sionna_rt_worker", fake_worker)

