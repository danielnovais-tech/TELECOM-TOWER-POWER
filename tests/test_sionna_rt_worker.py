# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for Tijolo 5 — sionna_rt_worker SQS poll + S3 raster upload."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts import sionna_rt_worker as worker


# ── Fakes ────────────────────────────────────────────────────────

class _FakeSQS:
    """Minimal SQS stub supporting receive/delete + a scripted backlog."""

    def __init__(self, backlog: list[dict] | None = None):
        self._backlog = list(backlog or [])
        self.deleted: list[str] = []
        self.receive_calls: list[dict] = []

    def receive_message(self, **kwargs):
        self.receive_calls.append(kwargs)
        if not self._backlog:
            return {}
        msg = self._backlog.pop(0)
        return {"Messages": [msg]}

    def delete_message(self, *, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)


class _FakeS3:
    """Minimal S3 stub backed by an in-memory bucket dict.

    ``store`` maps ``(bucket, key) -> bytes``. ``download_file`` writes
    bytes to disk; ``upload_file`` reads from disk back into the dict.
    """

    def __init__(self, store: dict[tuple[str, str], bytes] | None = None):
        self.store: dict[tuple[str, str], bytes] = dict(store or {})
        self.uploads: list[tuple[str, str, str]] = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class _Paginator:
            def paginate(self, *, Bucket, Prefix=""):
                contents = [
                    {"Key": k, "Size": len(v)}
                    for (b, k), v in outer.store.items()
                    if b == Bucket and k.startswith(Prefix)
                ]
                yield {"Contents": contents}
        return _Paginator()

    def download_file(self, Bucket, Key, Filename):
        data = self.store[(Bucket, Key)]
        os.makedirs(os.path.dirname(Filename) or ".", exist_ok=True)
        with open(Filename, "wb") as f:
            f.write(data)

    def upload_file(self, Filename, Bucket, Key):
        with open(Filename, "rb") as f:
            self.store[(Bucket, Key)] = f.read()
        self.uploads.append((Filename, Bucket, Key))


# ── Schema parsing ───────────────────────────────────────────────

def _good_job_dict(**overrides) -> dict:
    base = {
        "job_id": "job-001",
        "scene_s3_uri": "s3://bkt/scenes/sp/",
        "result_s3_uri": "s3://bkt/results/job-001.npz",
        "frequency_hz": 28e9,
        "tx": {"lat": -23.55, "lon": -46.64, "height_m": 30.0,
               "power_dbm": 43.0},
        "raster_grid": {"rows": 8, "cols": 8,
                        "bbox": [-23.56, -46.66, -23.54, -46.62]},
    }
    base.update(overrides)
    return base


def test_parse_job_message_ok():
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    assert job.job_id == "job-001"
    assert job.scene_s3_uri.endswith("/")
    assert job.rows == 8 and job.cols == 8
    assert job.frequency_hz == 28e9


def test_parse_appends_trailing_slash():
    j = worker.parse_job_message(json.dumps(
        _good_job_dict(scene_s3_uri="s3://bkt/scenes/sp")))
    assert j.scene_s3_uri == "s3://bkt/scenes/sp/"


@pytest.mark.parametrize("mutator,expected_substr", [
    (lambda d: d.pop("job_id"), "job_id"),
    (lambda d: d.update(scene_s3_uri="https://x"), "s3://"),
    (lambda d: d.update(result_s3_uri="x"), "s3://"),
    (lambda d: d.update(frequency_hz=1.0), "frequency_hz"),
    (lambda d: d.update(frequency_hz=1e15), "frequency_hz"),
    (lambda d: d["tx"].pop("lat"), "tx missing"),
    (lambda d: d["tx"].update(lat=999), "tx.lat"),
    (lambda d: d["tx"].update(lon=999), "tx.lon"),
    (lambda d: d["tx"].update(height_m=-1), "tx.height_m"),
    (lambda d: d["raster_grid"].update(rows=0), "rows/cols"),
    (lambda d: d["raster_grid"].update(rows=3000, cols=3000), "too large"),
    (lambda d: d["raster_grid"].update(bbox=[1, 2, 3]), "bbox"),
    (lambda d: d["raster_grid"].update(bbox=[10, 20, 5, 30]), "ordering"),
])
def test_parse_job_message_rejects(mutator, expected_substr):
    d = _good_job_dict()
    mutator(d)
    with pytest.raises(ValueError, match=expected_substr):
        worker.parse_job_message(json.dumps(d))


def test_parse_rejects_non_json():
    with pytest.raises(ValueError, match="JSON"):
        worker.parse_job_message("not json")


def test_parse_rejects_non_object():
    with pytest.raises(ValueError, match="object"):
        worker.parse_job_message("[1,2,3]")


# ── S3 helpers ───────────────────────────────────────────────────

def test_split_s3_uri():
    assert worker._split_s3_uri("s3://bkt/a/b") == ("bkt", "a/b")
    assert worker._split_s3_uri("s3://bkt/") == ("bkt", "")


def test_split_s3_uri_rejects():
    with pytest.raises(ValueError):
        worker._split_s3_uri("https://x")
    with pytest.raises(ValueError):
        worker._split_s3_uri("s3:///key")


def test_download_scene_bundle(tmp_path):
    s3 = _FakeS3({
        ("bkt", "scenes/sp/manifest.json"): b'{"x": 1}',
        ("bkt", "scenes/sp/scene.xml"): b"<scene/>",
        ("bkt", "scenes/sp/buildings.ply"): b"plydata",
        ("bkt", "scenes/other/skip.txt"): b"no",
    })
    out = worker.download_scene_bundle(
        "s3://bkt/scenes/sp/", str(tmp_path), s3=s3,
    )
    assert len(out) == 3
    assert (tmp_path / "manifest.json").read_bytes() == b'{"x": 1}'
    assert (tmp_path / "scene.xml").read_bytes() == b"<scene/>"


def test_download_scene_bundle_empty_raises(tmp_path):
    s3 = _FakeS3()
    with pytest.raises(FileNotFoundError):
        worker.download_scene_bundle(
            "s3://bkt/missing/", str(tmp_path), s3=s3,
        )


def test_upload_raster(tmp_path):
    p = tmp_path / "r.npz"
    p.write_bytes(b"npzdata")
    s3 = _FakeS3()
    worker.upload_raster(str(p), "s3://bkt/results/r.npz", s3=s3)
    assert s3.store[("bkt", "results/r.npz")] == b"npzdata"


# ── Raster compute + write ───────────────────────────────────────

def test_compute_raster_loss_shape_and_finite(tmp_path):
    np = pytest.importorskip("numpy")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    arr = worker.compute_raster_loss(str(tmp_path), job)
    assert arr.shape == (8, 8)
    assert arr.dtype == np.float32
    # Loss should increase with distance — corner > centre
    centre = arr[4, 4]
    corner = arr[0, 0]
    assert corner > centre
    assert np.isfinite(arr).all()


# ── Tracer-backend registry (T7) ─────────────────────────────────

def test_select_tracer_default_is_fspl_stub(monkeypatch):
    monkeypatch.delenv("SIONNA_RT_BACKEND", raising=False)
    t = worker.select_tracer()
    assert t.name == "fspl_stub"
    assert isinstance(t, worker._FsplStubTracer)


def test_select_tracer_env_var(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_BACKEND", "fspl_stub")
    assert worker.select_tracer().name == "fspl_stub"


def test_select_tracer_unknown_raises(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_BACKEND", "ray-with-pixie-dust")
    with pytest.raises(ValueError, match="unknown tracer backend"):
        worker.select_tracer()


def test_select_tracer_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_BACKEND", "totally-bogus")
    # Explicit name wins; env is ignored.
    assert worker.select_tracer("fspl_stub").name == "fspl_stub"


def test_compute_raster_loss_routes_through_registry(monkeypatch, tmp_path):
    """compute_raster_loss must delegate to the selected backend."""
    np = pytest.importorskip("numpy")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))

    sentinel = np.full((job.rows, job.cols), -7.0, dtype="float32")

    class _FakeTracer:
        name = "fake"

        def trace(self, scene_dir, job):
            return sentinel

    monkeypatch.setattr(worker, "select_tracer", lambda: _FakeTracer())
    arr = worker.compute_raster_loss(str(tmp_path), job)
    assert arr is sentinel


def test_fspl_parity_via_class(tmp_path):
    """Class API must match the function's output bit-for-bit (no regression)."""
    np = pytest.importorskip("numpy")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    arr_func = worker.compute_raster_loss(str(tmp_path), job)
    arr_class = worker._FsplStubTracer().trace(str(tmp_path), job)
    assert np.array_equal(arr_func, arr_class)


def test_sionna_rt_tracer_raises_when_deps_missing():
    """When mitsuba / sionna_rt aren't installed, construction fails loud."""
    # CI doesn't have the GPU stack; this is the expected path.
    try:
        import mitsuba  # type: ignore[import-not-found]  # noqa: F401
        import sionna_rt  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="not installed"):
            worker._SionnaRtTracer()
        return
    pytest.skip("mitsuba+sionna_rt installed; cannot test missing-deps path")


def test_sionna_rt_select_via_env_propagates_runtime_error(monkeypatch):
    """select_tracer('sionna_rt') must surface the missing-deps RuntimeError
    rather than swallowing it — ops needs to see the failure at boot."""
    try:
        import mitsuba  # type: ignore[import-not-found]  # noqa: F401
        import sionna_rt  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        monkeypatch.setenv("SIONNA_RT_BACKEND", "sionna_rt")
        with pytest.raises(RuntimeError, match="not installed"):
            worker.select_tracer()
        return
    pytest.skip("mitsuba+sionna_rt installed; cannot test missing-deps path")


# ── Mitsuba variant + scene helpers (T8) ─────────────────────────

class _FakeMi:
    """Minimal ``mitsuba`` stand-in used by T8 unit tests."""

    def __init__(self, variants):
        self._variants = list(variants)
        self.set_variant_calls: list[str] = []

    def variants(self):
        return list(self._variants)

    def set_variant(self, name):
        self.set_variant_calls.append(name)


def test_select_mitsuba_variant_prefers_cuda():
    mi = _FakeMi(["cuda_ad_rgb", "llvm_ad_rgb", "scalar_rgb"])
    assert worker._select_mitsuba_variant(mi) == "cuda_ad_rgb"


def test_select_mitsuba_variant_falls_back_to_llvm():
    mi = _FakeMi(["llvm_ad_rgb", "scalar_rgb"])
    assert worker._select_mitsuba_variant(mi) == "llvm_ad_rgb"


def test_select_mitsuba_variant_falls_back_to_scalar():
    mi = _FakeMi(["scalar_rgb"])
    assert worker._select_mitsuba_variant(mi) == "scalar_rgb"


def test_select_mitsuba_variant_raises_when_no_preferred():
    mi = _FakeMi(["scalar_mono"])
    with pytest.raises(RuntimeError, match="no supported Mitsuba variant"):
        worker._select_mitsuba_variant(mi)


def test_select_mitsuba_variant_env_pin(monkeypatch):
    monkeypatch.setenv("MITSUBA_VARIANT", "scalar_rgb")
    mi = _FakeMi(["cuda_ad_rgb", "llvm_ad_rgb", "scalar_rgb"])
    assert worker._select_mitsuba_variant(mi) == "scalar_rgb"


def test_select_mitsuba_variant_env_pin_unavailable_raises(monkeypatch):
    monkeypatch.setenv("MITSUBA_VARIANT", "scalar_rgb")
    mi = _FakeMi(["cuda_ad_rgb", "llvm_ad_rgb"])
    with pytest.raises(RuntimeError, match="not available"):
        worker._select_mitsuba_variant(mi)


def test_load_manifest_ok(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))
    m = worker._load_manifest(str(tmp_path))
    assert m["aoi_name"] == "sp-centro"


def test_load_manifest_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        worker._load_manifest(str(tmp_path))


def test_load_manifest_malformed_raises(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json")
    with pytest.raises(ValueError, match="malformed"):
        worker._load_manifest(str(tmp_path))


def test_resolve_scene_xml_ok(tmp_path):
    (tmp_path / "scene.xml").write_text("<scene/>")
    assert worker._resolve_scene_xml(str(tmp_path)) == str(tmp_path / "scene.xml")


def test_resolve_scene_xml_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="scene.xml missing"):
        worker._resolve_scene_xml(str(tmp_path))


# ── _SionnaRtTracer.trace() — real body via mocked stack (T8) ────

class _FakeSionnaScene:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.frequency = None
        self.tx_array = None
        self.rx_array = None
        self.transmitters: list = []

    def add(self, obj):
        self.transmitters.append(obj)


class _FakeCoverageMap:
    def __init__(self, path_gain):
        self.path_gain = path_gain


class _FakePathSolver:
    """Captures every coverage-map call for assertions."""

    last_kwargs: dict | None = None

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        _FakePathSolver.last_kwargs = kwargs
        # Synthesise a deterministic path_gain raster: a (rows, cols)
        # array of small but non-zero linear gains decreasing with
        # cell index, so loss_db is finite and increasing.
        scene = kwargs["scene"]
        cell_w, cell_h = kwargs["cell_size"]
        size_x, size_y = kwargs["size"]
        cols = int(round(size_x / cell_w))
        rows = int(round(size_y / cell_h))
        np = pytest.importorskip("numpy")
        return _FakeCoverageMap(np.full((rows, cols), 1e-9, dtype="float64"))


class _FakeTransmitter:
    last_kwargs: dict | None = None

    def __init__(self, *, name, position):
        self.name = name
        self.position = position
        _FakeTransmitter.last_kwargs = {"name": name, "position": position}


class _FakePlanarArray:
    last_kwargs: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakePlanarArray.last_kwargs.append(kwargs)


def _fake_load_scene(xml_path):
    return _FakeSionnaScene(xml_path)


def _install_fake_sionna_rt(monkeypatch):
    """Wire fake ``mitsuba`` + ``sionna_rt`` into ``sys.modules``."""
    import types

    fake_mi = types.SimpleNamespace(
        variants=lambda: ["llvm_ad_rgb", "scalar_rgb"],
        _set_variant_calls=[],
    )
    def _set_variant(name):
        fake_mi._set_variant_calls.append(name)
    fake_mi.set_variant = _set_variant
    monkeypatch.setitem(sys.modules, "mitsuba", fake_mi)

    _FakePlanarArray.last_kwargs = []
    _FakePathSolver.last_kwargs = None
    _FakeTransmitter.last_kwargs = None

    fake_srt = types.SimpleNamespace(
        load_scene=_fake_load_scene,
        PlanarArray=_FakePlanarArray,
        Transmitter=_FakeTransmitter,
        PathSolver=_FakePathSolver,
    )
    fake_sionna = types.ModuleType("sionna")
    fake_sionna.rt = fake_srt  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sionna", fake_sionna)
    monkeypatch.setitem(sys.modules, "sionna.rt", fake_srt)
    monkeypatch.setitem(sys.modules, "sionna_rt", fake_srt)
    return fake_mi, fake_srt


def test_sionna_rt_tracer_trace_end_to_end(monkeypatch, tmp_path):
    np = pytest.importorskip("numpy")
    fake_mi, fake_srt = _install_fake_sionna_rt(monkeypatch)

    # Scene bundle on disk — manifest + scene.xml
    (tmp_path / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))
    (tmp_path / "scene.xml").write_text("<scene/>")

    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    tracer = worker._SionnaRtTracer()
    arr = tracer.trace(str(tmp_path), job)

    # Output contract
    assert arr.shape == (job.rows, job.cols)
    assert arr.dtype == np.float32
    # path_gain=1e-9 → loss = -10*log10(1e-9) = 90 dB
    assert np.allclose(arr, 90.0, atol=1e-3)

    # Variant was selected + applied
    assert fake_mi._set_variant_calls == ["llvm_ad_rgb"]

    # Two PlanarArray builds: tx_array + rx_array
    assert len(_FakePlanarArray.last_kwargs) == 2
    for kw in _FakePlanarArray.last_kwargs:
        assert kw["pattern"] == "iso"
        assert kw["polarization"] == "V"

    # Transmitter placed at the projected TX coords (z = tx_height_m).
    tx = _FakeTransmitter.last_kwargs
    assert tx["name"] == "tx"
    assert tx["position"][2] == job.tx_height_m

    # PathSolver received scene-frequency-set + correct grid geometry.
    cm_kwargs = _FakePathSolver.last_kwargs
    assert cm_kwargs["scene"].frequency == job.frequency_hz
    assert cm_kwargs["scene"].transmitters  # tx was added
    cell_w, cell_h = cm_kwargs["cell_size"]
    size_x, size_y = cm_kwargs["size"]
    assert abs(size_x / cell_w - job.cols) < 1e-6
    assert abs(size_y / cell_h - job.rows) < 1e-6
    # rx height defaults to 1.5 m
    assert cm_kwargs["center"][2] == 1.5


def test_sionna_rt_tracer_honours_env_overrides(monkeypatch, tmp_path):
    fake_mi, _ = _install_fake_sionna_rt(monkeypatch)
    monkeypatch.setenv("SIONNA_RT_MAX_DEPTH", "9")
    monkeypatch.setenv("SIONNA_RT_SAMPLES", "12345")
    monkeypatch.setenv("SIONNA_RT_RX_HEIGHT_M", "3.25")
    monkeypatch.setenv("MITSUBA_VARIANT", "scalar_rgb")

    (tmp_path / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))
    (tmp_path / "scene.xml").write_text("<scene/>")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))

    arr = worker._SionnaRtTracer().trace(str(tmp_path), job)
    assert arr.shape == (job.rows, job.cols)

    cm_kwargs = _FakePathSolver.last_kwargs
    assert cm_kwargs["max_depth"] == 9
    assert cm_kwargs["samples_per_tx"] == 12345
    assert cm_kwargs["center"][2] == 3.25
    assert fake_mi._set_variant_calls == ["scalar_rgb"]


def test_sionna_rt_tracer_missing_scene_xml(monkeypatch, tmp_path):
    _install_fake_sionna_rt(monkeypatch)
    (tmp_path / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    with pytest.raises(FileNotFoundError, match="scene.xml"):
        worker._SionnaRtTracer().trace(str(tmp_path), job)


def test_sionna_rt_tracer_missing_manifest(monkeypatch, tmp_path):
    _install_fake_sionna_rt(monkeypatch)
    (tmp_path / "scene.xml").write_text("<scene/>")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        worker._SionnaRtTracer().trace(str(tmp_path), job)


def test_sionna_rt_tracer_clamps_zero_path_gain(monkeypatch, tmp_path):
    """A pixel with literal path_gain=0 must not blow up to inf/NaN."""
    np = pytest.importorskip("numpy")
    _install_fake_sionna_rt(monkeypatch)

    # Override PathSolver to emit a zero-gain map.
    class _ZeroSolver:
        def __init__(self): pass

        def __call__(self, **kwargs):
            cell_w, cell_h = kwargs["cell_size"]
            sx, sy = kwargs["size"]
            cols = int(round(sx / cell_w))
            rows = int(round(sy / cell_h))
            _FakePathSolver.last_kwargs = kwargs
            return _FakeCoverageMap(np.zeros((rows, cols), dtype="float64"))

    sys.modules["sionna_rt"].PathSolver = _ZeroSolver  # type: ignore[attr-defined]

    (tmp_path / "manifest.json").write_text(json.dumps(_GOOD_MANIFEST))
    (tmp_path / "scene.xml").write_text("<scene/>")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    arr = worker._SionnaRtTracer().trace(str(tmp_path), job)
    assert np.isfinite(arr).all()
    # -10*log10(1e-30) = 300 dB; clamped sentinel.
    assert np.allclose(arr, 300.0, atol=1e-3)


def test_write_raster_npz_roundtrip(tmp_path):
    np = pytest.importorskip("numpy")
    job = worker.parse_job_message(json.dumps(_good_job_dict()))
    arr = np.full((4, 4), 100.0, dtype="float32")
    out = tmp_path / "r.npz"
    worker.write_raster_npz(arr, job, str(out))
    z = np.load(out)
    assert z["loss_db"].shape == (4, 4)
    assert float(z["frequency_hz"]) == job.frequency_hz
    assert list(z["bbox"]) == [job.bbox_south, job.bbox_west,
                               job.bbox_north, job.bbox_east]
    assert str(z["job_id"]) == job.job_id


# ── Manifest validation ──────────────────────────────────────────

_GOOD_MANIFEST = {
    "schema_version": 1,
    "aoi_name": "sp-centro",
    "bbox": [-23.56, -46.66, -23.54, -46.62],
    "frequencies_hz": [28e9],
    "p2040_table_version": "1",
    "implementation_status": "complete",
}


def test_validate_manifest_ok(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_GOOD_MANIFEST))
    assert worker._validate_manifest(str(p)) is None


def test_validate_manifest_missing_status(tmp_path):
    p = tmp_path / "manifest.json"
    bad = dict(_GOOD_MANIFEST, implementation_status="data-only")
    p.write_text(json.dumps(bad))
    assert "data-only" in worker._validate_manifest(str(p))


def test_validate_manifest_missing_file(tmp_path):
    assert "not found" in worker._validate_manifest(
        str(tmp_path / "nope.json"))


# ── End-to-end ───────────────────────────────────────────────────

def _make_scene_store(bucket: str, prefix: str) -> dict[tuple[str, str], bytes]:
    return {
        (bucket, f"{prefix}manifest.json"): json.dumps(_GOOD_MANIFEST).encode(),
        (bucket, f"{prefix}scene.xml"): b"<scene/>",
        (bucket, f"{prefix}buildings.ply"): b"ply",
        (bucket, f"{prefix}terrain.ply"): b"ply",
    }


def test_process_message_happy_path(tmp_path):
    pytest.importorskip("numpy")
    s3 = _FakeS3(_make_scene_store("bkt", "scenes/sp/"))
    sqs = _FakeSQS()
    body = json.dumps(_good_job_dict())
    msg = {"Body": body, "ReceiptHandle": "rh-1"}
    res = worker.process_message(
        msg, "https://sqs/queue", sqs=sqs, s3=s3,
        work_dir_root=str(tmp_path),
    )
    assert res["status"] == "ok"
    assert res["raster_bytes"] > 0
    assert sqs.deleted == ["rh-1"]
    assert ("bkt", "results/job-001.npz") in s3.store


def test_process_message_poison_pill_deleted(tmp_path):
    s3 = _FakeS3()
    sqs = _FakeSQS()
    msg = {"Body": "not json", "ReceiptHandle": "rh-poison"}
    res = worker.process_message(
        msg, "https://sqs/queue", sqs=sqs, s3=s3,
        work_dir_root=str(tmp_path),
    )
    assert res["status"] == "rejected"
    assert sqs.deleted == ["rh-poison"]


def test_process_message_bad_manifest_keeps_msg(tmp_path):
    bad_store = {
        ("bkt", "scenes/sp/manifest.json"): json.dumps(
            dict(_GOOD_MANIFEST, implementation_status="data-only")
        ).encode(),
    }
    s3 = _FakeS3(bad_store)
    sqs = _FakeSQS()
    msg = {"Body": json.dumps(_good_job_dict()), "ReceiptHandle": "rh-2"}
    res = worker.process_message(
        msg, "https://sqs/queue", sqs=sqs, s3=s3,
        work_dir_root=str(tmp_path),
    )
    assert res["status"] == "retry"
    assert sqs.deleted == []  # left for redrive


def test_process_message_s3_failure_keeps_msg(tmp_path):
    s3 = _FakeS3()  # empty → download raises FileNotFoundError
    sqs = _FakeSQS()
    msg = {"Body": json.dumps(_good_job_dict()), "ReceiptHandle": "rh-3"}
    res = worker.process_message(
        msg, "https://sqs/queue", sqs=sqs, s3=s3,
        work_dir_root=str(tmp_path),
    )
    assert res["status"] == "retry"
    assert sqs.deleted == []


def test_poll_loop_idle_exit(tmp_path):
    sqs = _FakeSQS()
    s3 = _FakeS3()
    out = worker.poll_loop(
        "https://sqs/queue", sqs=sqs, s3=s3,
        idle_exit=True, wait_seconds=0,
    )
    assert out == []
    assert len(sqs.receive_calls) == 1


def test_poll_loop_processes_then_exits(tmp_path):
    pytest.importorskip("numpy")
    s3 = _FakeS3(_make_scene_store("bkt", "scenes/sp/"))
    msg = {"Body": json.dumps(_good_job_dict()), "ReceiptHandle": "rh-x"}
    sqs = _FakeSQS(backlog=[msg])
    out = worker.poll_loop(
        "https://sqs/queue", sqs=sqs, s3=s3,
        once=True, wait_seconds=0,
    )
    assert len(out) == 1
    assert out[0]["status"] == "ok"
    assert sqs.deleted == ["rh-x"]


# ── CLI ──────────────────────────────────────────────────────────

def test_main_probe_emits_json(capsys, monkeypatch):
    rc = worker.main(["--probe"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, dict)


def test_main_poll_requires_queue_url(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_DISABLED", "0")
    monkeypatch.delenv("SIONNA_RT_QUEUE_URL", raising=False)
    rc = worker.main(["--poll"])
    assert rc == 4


def test_main_poll_refuses_when_disabled(monkeypatch):
    monkeypatch.setenv("SIONNA_RT_DISABLED", "1")
    monkeypatch.setenv("SIONNA_RT_QUEUE_URL", "https://q")
    rc = worker.main(["--poll"])
    assert rc == 3


def test_main_no_action(capsys):
    with pytest.raises(SystemExit):
        worker.main([])
