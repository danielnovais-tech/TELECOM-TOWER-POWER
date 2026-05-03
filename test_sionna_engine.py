# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for ``rf_engines.sionna_engine``.

We don't ship TFLite (or full TensorFlow) in the test environment —
those run on the GPU training boxes only. The runtime cares about
three things:

1. ``is_available()`` is False until both artefact + sidecar exist
   AND ``SIONNA_DISABLED=0``.
2. Schema-version mismatch in the sidecar makes the engine refuse to
   load (fail-closed contract).
3. Once loaded with a stub interpreter, predictions go through
   :func:`build_features` → standardisation → invoke and produce a
   ``LossEstimate`` with the expected metadata.

We patch :func:`rf_engines.sionna_engine._load_interpreter` with a
``DummyInterpreter`` for case (3) — that exercises every line of the
glue without requiring TF.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rf_engines._sionna_features import FEATURE_DIM, FEATURE_SCHEMA_VERSION
from rf_engines.sionna_engine import SionnaEngine
import rf_engines.sionna_engine as sionna_mod


def _write_sidecar(path: Path, *, schema: str = FEATURE_SCHEMA_VERSION,
                   dim: int = FEATURE_DIM) -> None:
    path.write_text(json.dumps({
        "schema_version": schema,
        "feature_dim": dim,
        "feature_names": [f"f{i}" for i in range(dim)],
        "mean": [0.0] * dim,
        "std": [1.0] * dim,
        "n_train": 1234,
        "trained_at": 1730000000,
    }))


class _DummyInterpreter:
    """Minimal stand-in for a tflite interpreter.

    Returns a fixed ``predicted_db`` regardless of input. That's all
    the engine glue needs to be exercised — the actual model is
    validated by ``coverage_diff_robot`` against drive-test labels.
    """

    def __init__(self, predicted_db: float = 120.5, dim: int = FEATURE_DIM):
        self._pred = np.float32(predicted_db)
        self._dim = dim
        self._last_x: np.ndarray | None = None

    def allocate_tensors(self) -> None:
        pass

    def get_input_details(self):
        return [{"index": 0, "shape": np.array([1, self._dim], dtype=np.int32)}]

    def get_output_details(self):
        return [{"index": 1, "shape": np.array([1, 1], dtype=np.int32)}]

    def set_tensor(self, idx: int, x: np.ndarray) -> None:
        self._last_x = x

    def invoke(self) -> None:
        pass

    def get_tensor(self, idx: int) -> np.ndarray:
        return np.array([[self._pred]], dtype=np.float32)


@pytest.fixture
def artefact_dir(tmp_path: Path) -> Path:
    """Create a model file (empty bytes; loader is patched) + valid sidecar."""
    model = tmp_path / "sionna_model.tflite"
    model.write_bytes(b"\x00\x00")  # contents irrelevant — loader is mocked
    sidecar = tmp_path / "sionna_features.json"
    _write_sidecar(sidecar)
    return tmp_path


def _make_engine(tmp_dir: Path, monkeypatch, *, disabled: str = "0",
                 interpreter=None) -> SionnaEngine:
    monkeypatch.setenv("SIONNA_DISABLED", disabled)
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(tmp_dir / "sionna_model.tflite"))
    monkeypatch.setenv("SIONNA_FEATURES_PATH", str(tmp_dir / "sionna_features.json"))
    monkeypatch.setattr(sionna_mod, "_load_interpreter",
                        lambda _p: interpreter or _DummyInterpreter())
    return SionnaEngine()


# Default kwargs for predict_basic_loss — same shape as drive-test rows.
_LINK = dict(
    f_hz=900e6,
    d_km=[i * 5 / 31 for i in range(32)],
    h_m=[100.0] * 32,
    htg=30.0,
    hrg=2.0,
    phi_t=-15.7, lam_t=-47.9,
    phi_r=-15.74, lam_r=-47.92,
    pol=2, zone=4,
)


def test_disabled_by_default():
    eng = SionnaEngine()
    # Without setting SIONNA_DISABLED=0, the engine MUST stay off
    # regardless of artefact presence — that is the production safety
    # contract until ops benchmarks the model.
    assert eng.is_available() is False
    assert eng.predict_basic_loss(**_LINK) is None


def test_missing_artefact_returns_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SIONNA_DISABLED", "0")
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(tmp_path / "nope.tflite"))
    eng = SionnaEngine()
    assert eng.is_available() is False


def test_missing_sidecar_fails_closed(tmp_path, monkeypatch):
    model = tmp_path / "sionna_model.tflite"
    model.write_bytes(b"\x00")
    monkeypatch.setenv("SIONNA_DISABLED", "0")
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(model))
    monkeypatch.setenv("SIONNA_FEATURES_PATH",
                       str(tmp_path / "missing.json"))
    monkeypatch.setattr(sionna_mod, "_load_interpreter",
                        lambda _p: _DummyInterpreter())
    eng = SionnaEngine()
    # Refusing to serve without normalisation stats prevents silent
    # systematic bias — see module docstring.
    assert eng.is_available() is False


def test_schema_mismatch_fails_closed(tmp_path, monkeypatch):
    model = tmp_path / "sionna_model.tflite"
    model.write_bytes(b"\x00")
    sidecar = tmp_path / "sionna_features.json"
    _write_sidecar(sidecar, schema="v0_legacy")
    monkeypatch.setenv("SIONNA_DISABLED", "0")
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(model))
    monkeypatch.setenv("SIONNA_FEATURES_PATH", str(sidecar))
    monkeypatch.setattr(sionna_mod, "_load_interpreter",
                        lambda _p: _DummyInterpreter())
    eng = SionnaEngine()
    assert eng.is_available() is False


def test_feature_dim_mismatch_fails_closed(tmp_path, monkeypatch):
    model = tmp_path / "sionna_model.tflite"
    model.write_bytes(b"\x00")
    sidecar = tmp_path / "sionna_features.json"
    _write_sidecar(sidecar, dim=FEATURE_DIM + 1)
    monkeypatch.setenv("SIONNA_DISABLED", "0")
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(model))
    monkeypatch.setenv("SIONNA_FEATURES_PATH", str(sidecar))
    monkeypatch.setattr(sionna_mod, "_load_interpreter",
                        lambda _p: _DummyInterpreter())
    eng = SionnaEngine()
    assert eng.is_available() is False


def test_predict_with_stub_interpreter(artefact_dir, monkeypatch):
    eng = _make_engine(artefact_dir, monkeypatch,
                       interpreter=_DummyInterpreter(predicted_db=132.4))
    assert eng.is_available() is True
    est = eng.predict_basic_loss(**_LINK)
    assert est is not None
    assert est.engine == "sionna"
    assert est.basic_loss_db == pytest.approx(132.4, abs=1e-3)
    assert est.confidence == 0.7
    assert est.runtime_ms is not None and est.runtime_ms >= 0
    assert est.extra["schema_version"] == FEATURE_SCHEMA_VERSION
    assert est.extra["n_train"] == 1234


def test_out_of_range_prediction_suppressed(artefact_dir, monkeypatch):
    # 999 dB is beyond any physical link — engine must return None and
    # let the registry fall back to P.1812.
    eng = _make_engine(artefact_dir, monkeypatch,
                       interpreter=_DummyInterpreter(predicted_db=999.0))
    assert eng.is_available() is True
    assert eng.predict_basic_loss(**_LINK) is None


def test_load_latches_on_failure(tmp_path, monkeypatch):
    """First _load() failure must be cached — the compare endpoint
    calls is_available() on every request and we don't want to keep
    hitting the disk."""
    monkeypatch.setenv("SIONNA_DISABLED", "0")
    monkeypatch.setenv("SIONNA_MODEL_PATH", str(tmp_path / "nope.tflite"))
    eng = SionnaEngine()
    assert eng.is_available() is False
    # Now create the file — without reset(), the engine should NOT
    # pick it up. This proves the latch works.
    (tmp_path / "nope.tflite").write_bytes(b"\x00")
    _write_sidecar(tmp_path / "sionna_features.json")
    monkeypatch.setenv("SIONNA_FEATURES_PATH",
                       str(tmp_path / "sionna_features.json"))
    monkeypatch.setattr(sionna_mod, "_load_interpreter",
                        lambda _p: _DummyInterpreter())
    assert eng.is_available() is False
    eng.reset()
    assert eng.is_available() is True
