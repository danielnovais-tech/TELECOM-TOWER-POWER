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
    _patch_sionna_rt(monkeypatch, loss_db=95.3)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    eng = SionnaRTEngine()
    est = eng.predict_basic_loss(**_LINK)
    assert est is not None
    assert est.engine == "sionna-rt"
    assert est.basic_loss_db == pytest.approx(95.3, abs=0.05)
    assert est.confidence == 1.0


def test_predict_basic_loss_hrg_used_as_rx_height(monkeypatch, tmp_path):
    """hrg must appear as the z-coordinate of the placed srt.Receiver."""
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    captured: list = []
    _patch_sionna_rt(monkeypatch, loss_db=80.0, capture=captured)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    SionnaRTEngine().predict_basic_loss(**_LINK)
    rx_calls = [c for c in captured if c.get("type") == "receiver" and c["name"] == "rx"]
    assert rx_calls, "srt.Receiver(name='rx') was not called"
    rx_z = rx_calls[0]["position"][2]
    assert rx_z == pytest.approx(_LINK["hrg"])


def test_predict_basic_loss_rx_outside_bbox_returns_none(monkeypatch, tmp_path):
    """An RX point outside the scene's bbox should return None, not raise."""
    import json as _json
    # Tight bbox that excludes _LINK's phi_r/lam_r
    tight_manifest = dict(_GOOD_MANIFEST)
    tight_manifest["bbox"] = [-23.55, -46.65, -23.50, -46.60]  # RX is further south/west
    (tmp_path / "scene.xml").write_text("<scene/>")
    (tmp_path / "manifest.json").write_text(_json.dumps(tight_manifest))
    _fake_gpu_stack(monkeypatch)
    _patch_sionna_rt(monkeypatch, loss_db=80.0)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    assert SionnaRTEngine().predict_basic_loss(**_LINK) is None


def test_predict_basic_loss_extra_contains_metadata(monkeypatch, tmp_path):
    """extra dict must carry rx_height_m, tx_height_m, mitsuba_variant, etc."""
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_sionna_rt(monkeypatch, loss_db=100.0)
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    est = SionnaRTEngine().predict_basic_loss(**_LINK)
    assert est is not None
    assert est.extra["rx_height_m"] == pytest.approx(_LINK["hrg"])
    assert est.extra["tx_height_m"] == pytest.approx(_LINK["htg"])
    assert "mitsuba_variant" in est.extra
    assert "frequency_hz" in est.extra


def test_predict_basic_loss_returns_none_on_solver_exception(monkeypatch, tmp_path):
    """PathSolver errors must fail closed (None), not 500."""
    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_sionna_rt(monkeypatch, exc=RuntimeError("GPU OOM"))
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.setenv("SIONNA_RT_SCENE_PATH", str(tmp_path))

    assert SionnaRTEngine().predict_basic_loss(**_LINK) is None


def test_predict_basic_loss_in_compare_loop(monkeypatch, tmp_path):
    """Engine participates normally in compare() when available."""
    from rf_engines.compare import compare

    _make_scene_dir(tmp_path)
    _fake_gpu_stack(monkeypatch)
    _patch_sionna_rt(monkeypatch, loss_db=110.0)
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
    assert by_name["sionna-rt"].basic_loss_db == pytest.approx(110.0, abs=0.05)
    assert by_name["sionna-rt"].delta_db == pytest.approx(10.0, abs=0.05)


# ── Helpers ────────────────────────────────────────────────────────

_GOOD_MANIFEST = {
    "schema_version": 1,
    "aoi_name": "sp-centro",
    # Covers _LINK TX (phi_t=-23.5, lam_t=-46.6) and RX (phi_r=-23.6, lam_r=-46.7)
    "bbox": [-23.65, -46.75, -23.45, -46.55],
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


def _patch_sionna_rt(monkeypatch, *, loss_db=None, exc=None, capture=None):
    """Replace sys.modules['sionna_rt'] with a full fake for _run_trace().

    The fake provides:
    * ``load_scene`` → a minimal scene stub
    * ``PlanarArray`` / ``Transmitter`` / ``Receiver`` → simple namespaces
    * ``PathSolver`` → a callable that returns ``Paths`` whose ``.a`` encodes
      ``loss_db`` as a single-path complex amplitude.

    When *capture* is a list, every call to ``srt.Receiver(...)`` appends
    ``{'name': …, 'position': …}`` so tests can assert on the receiver
    coordinates (e.g. that ``hrg`` ends up as the z-component).
    """
    import numpy as np  # type: ignore[import-not-found]

    class _FakeScene:
        frequency = None
        tx_array = None
        rx_array = None
        def add(self, obj): pass

    class _FakePaths:
        def __init__(self):
            # |a|² = path_gain = 10^(−loss_db/10).  Shape chosen to match
            # real Sionna RT: [batch, num_rx, rx_ant, num_tx, tx_ant, paths].
            pg = 10 ** (-float(loss_db) / 10.0) if loss_db is not None else 1.0
            self.a = np.full((1, 1, 1, 1, 1, 1), np.sqrt(pg), dtype=np.complex64)

    class _FakePathSolver:
        def __call__(self, *, scene, max_depth=5):
            if capture is not None:
                capture.append({"type": "solver_call", "max_depth": max_depth})
            if exc is not None:
                raise exc
            return _FakePaths()

    def _fake_receiver(*, name, position):
        if capture is not None:
            capture.append({"type": "receiver", "name": name, "position": position})
        return types.SimpleNamespace(name=name, position=position)

    fake_srt = types.SimpleNamespace(
        load_scene=lambda _: _FakeScene(),
        PlanarArray=lambda **kw: None,
        Transmitter=lambda **kw: None,
        Receiver=_fake_receiver,
        PathSolver=_FakePathSolver,
    )
    monkeypatch.setitem(sys.modules, "sionna_rt", fake_srt)

