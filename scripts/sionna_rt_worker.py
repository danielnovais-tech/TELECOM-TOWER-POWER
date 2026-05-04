# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""scripts/sionna_rt_worker.py — GPU worker SQS consumer.

Tijolo 1 (2026-05-03): scaffold + ``--probe``.
Tijolo 5 (2026-05-04): real SQS poll loop + S3 raster upload.

The worker:

1. Long-polls ``$SIONNA_RT_QUEUE_URL`` for one job message at a time.
2. Validates the message schema (``job_id``, ``scene_s3_uri``,
   ``tx``, ``frequency_hz``, ``raster_grid``, ``result_s3_uri``).
3. Downloads the scene bundle from S3 to a fresh temp directory.
4. Validates ``manifest.json`` (``implementation_status='complete'``).
5. Computes a per-pixel basic-loss raster (stub — replaced by the
   real GPU trace in a follow-up brick).
6. Writes the raster as ``.npz`` (numpy + bbox + frequency metadata).
7. Uploads it to ``result_s3_uri``.
8. Deletes the SQS message on success. Failures leave the message
   for the queue's redrive policy to handle.

The actual GPU ray-trace is intentionally still a stub: tijolos 6+
land Mitsuba ``load_file`` + Sionna ``PathSolver`` integration. T5
unblocks the *plumbing* — operators can already enqueue real S3
job descriptors and observe the worker upload deterministic stub
rasters end-to-end.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger("sionna_rt_worker")

_DEFAULT_WAIT_SECONDS = 20  # SQS long-poll max
_DEFAULT_VISIBILITY = 300   # 5 min — bigger than any plausible trace


# ── GPU stack probe ──────────────────────────────────────────────

def _probe_gpu_stack() -> dict:
    """Import torch/mitsuba/drjit/sionna and report versions + CUDA."""
    info: dict = {}
    try:
        import torch  # type: ignore[import-not-found]
        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["device_name"] = torch.cuda.get_device_name(0)
            info["device_capability"] = list(torch.cuda.get_device_capability(0))
    except Exception as ex:
        info["torch_error"] = f"{type(ex).__name__}: {ex}"
    for mod_name in ("mitsuba", "drjit", "sionna"):
        try:
            mod = __import__(mod_name)
            info[mod_name] = getattr(mod, "__version__", "unknown")
        except Exception as ex:
            info[f"{mod_name}_error"] = f"{type(ex).__name__}: {ex}"
    return info


# ── Manifest validation ──────────────────────────────────────────

def _validate_manifest(path: str) -> Optional[str]:
    """Return None on success, an error string on failure."""
    if not os.path.isfile(path):
        return f"manifest not found at {path}"
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception as ex:
        return f"manifest parse failed: {ex}"
    required = (
        "schema_version", "aoi_name", "bbox", "frequencies_hz",
        "p2040_table_version", "implementation_status",
    )
    missing = [k for k in required if k not in m]
    if missing:
        return f"manifest missing keys: {missing}"
    if m["implementation_status"] != "complete":
        return (
            f"manifest implementation_status='{m['implementation_status']}'; "
            "refusing to launch until scene builder reaches 'complete'"
        )
    return None


# ── Job message schema ───────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Job:
    job_id: str
    scene_s3_uri: str
    tx_lat: float
    tx_lon: float
    tx_height_m: float
    tx_power_dbm: float
    frequency_hz: float
    rows: int
    cols: int
    bbox_south: float
    bbox_west: float
    bbox_north: float
    bbox_east: float
    result_s3_uri: str


def parse_job_message(body: str) -> Job:
    """Parse + validate an SQS message body into a :class:`Job`.

    Raises ``ValueError`` with a descriptive message on any schema
    violation. Designed to be fail-fast: an invalid job is *not*
    retried — the message is deleted with an error log.
    """
    try:
        m = json.loads(body)
    except json.JSONDecodeError as ex:
        raise ValueError(f"job body is not valid JSON: {ex}") from ex
    if not isinstance(m, dict):
        raise ValueError("job body must be a JSON object")

    def _req(key: str, t: type) -> Any:
        if key not in m:
            raise ValueError(f"missing required field: {key!r}")
        v = m[key]
        if not isinstance(v, t):
            raise ValueError(
                f"field {key!r} must be {t.__name__}, got {type(v).__name__}"
            )
        return v

    job_id = _req("job_id", str)
    scene = _req("scene_s3_uri", str)
    if not scene.startswith("s3://"):
        raise ValueError("scene_s3_uri must start with s3://")
    result = _req("result_s3_uri", str)
    if not result.startswith("s3://"):
        raise ValueError("result_s3_uri must start with s3://")
    freq = float(_req("frequency_hz", (int, float)))  # type: ignore[arg-type]
    if not (1e6 <= freq <= 3e11):
        raise ValueError(f"frequency_hz out of plausible range: {freq}")

    tx = _req("tx", dict)
    for k in ("lat", "lon", "height_m"):
        if k not in tx:
            raise ValueError(f"tx missing required field: {k!r}")
    lat = float(tx["lat"]); lon = float(tx["lon"])
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"tx.lat out of range: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"tx.lon out of range: {lon}")
    height = float(tx["height_m"])
    if height < 0:
        raise ValueError(f"tx.height_m must be >= 0: {height}")
    power = float(tx.get("power_dbm", 43.0))

    grid = _req("raster_grid", dict)
    rows = int(grid.get("rows", 0))
    cols = int(grid.get("cols", 0))
    if rows <= 0 or cols <= 0:
        raise ValueError(f"raster_grid rows/cols must be > 0: {rows}x{cols}")
    if rows * cols > 4_000_000:
        raise ValueError(
            f"raster_grid too large ({rows}x{cols} > 4M cells); "
            "split before enqueueing"
        )
    bbox = grid.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("raster_grid.bbox must be a 4-list [south,west,north,east]")
    s, w, n, e = (float(b) for b in bbox)
    if s >= n or w >= e:
        raise ValueError(f"raster_grid.bbox invalid ordering: {bbox}")

    return Job(
        job_id=job_id, scene_s3_uri=scene if scene.endswith("/") else scene + "/",
        tx_lat=lat, tx_lon=lon, tx_height_m=height, tx_power_dbm=power,
        frequency_hz=freq, rows=rows, cols=cols,
        bbox_south=s, bbox_west=w, bbox_north=n, bbox_east=e,
        result_s3_uri=result,
    )


# ── S3 helpers ───────────────────────────────────────────────────

def _split_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 URI: {uri}")
    p = urllib.parse.urlparse(uri)
    bucket = p.netloc
    key = p.path.lstrip("/")
    if not bucket:
        raise ValueError(f"s3 URI missing bucket: {uri}")
    return bucket, key


def download_scene_bundle(scene_s3_uri: str, dest_dir: str, *, s3) -> list[str]:
    """Download every object under ``scene_s3_uri`` to ``dest_dir``.

    Returns the list of local file paths written. Pessimistic:
    raises if no objects are found (operator probably typoed the URI).
    """
    bucket, prefix = _split_s3_uri(scene_s3_uri)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    paginator = s3.get_paginator("list_objects_v2")
    written: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            rel = key[len(prefix):] if key.startswith(prefix) else key
            if not rel:
                continue
            local = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(local) or dest_dir, exist_ok=True)
            s3.download_file(bucket, key, local)
            written.append(local)
    if not written:
        raise FileNotFoundError(
            f"no objects found at {scene_s3_uri} (bucket={bucket} prefix={prefix})"
        )
    return written


def upload_raster(local_path: str, result_s3_uri: str, *, s3) -> None:
    bucket, key = _split_s3_uri(result_s3_uri)
    if not key:
        raise ValueError(f"result_s3_uri missing key: {result_s3_uri}")
    s3.upload_file(local_path, bucket, key)


# ── Tracer backends ──────────────────────────────────────────────
#
# Tijolo 7 (2026-05-03): pluggable tracer registry. The contract is
# ``RFTracer.trace(scene_dir, job) -> np.ndarray of shape (rows, cols)
# dtype float32`` (basic loss in dB).
#
# Two backends ship today:
#   - ``fspl_stub`` (default): closed-form Friis FSPL, no GPU. The
#     same numbers Tijolo 5 produced. Used by all tests/CI and by
#     prod until GPU instances roll out.
#   - ``sionna_rt``: real Mitsuba 3 / Sionna RT trace. Requires
#     ``mitsuba``+``sionna_rt`` and a CUDA-capable host. Construction
#     raises ``RuntimeError`` when those imports fail so ops can't
#     accidentally enable an unusable backend. The actual trace is
#     deferred to Tijolo 8.
#
# Selection: ``$SIONNA_RT_BACKEND`` (default ``fspl_stub``).

class _FsplStubTracer:
    """Closed-form Friis FSPL — deterministic, no GPU."""

    name = "fspl_stub"

    def trace(self, scene_dir: str, job: "Job") -> "Any":  # noqa: ARG002
        import math
        import numpy as np  # type: ignore[import-not-found]

        rows, cols = job.rows, job.cols
        lats = np.linspace(job.bbox_north, job.bbox_south, rows)
        lons = np.linspace(job.bbox_west, job.bbox_east, cols)
        LAT, LON = np.meshgrid(lats, lons, indexing="ij")
        R = 6_371_008.8
        lat0 = math.radians(job.tx_lat)
        dlat = np.radians(LAT - job.tx_lat)
        dlon = np.radians(LON - job.tx_lon) * math.cos(lat0)
        d_m = np.hypot(dlat, dlon) * R
        d_m = np.maximum(d_m, 1.0)  # avoid log(0) at TX pixel
        # FSPL: 32.45 + 20·log10(f_MHz) + 20·log10(d_km)
        f_mhz = job.frequency_hz / 1e6
        fspl_db = 32.45 + 20.0 * np.log10(f_mhz) + 20.0 * np.log10(d_m / 1000.0)
        return fspl_db.astype("float32")


class _SionnaRtTracer:
    """Real Mitsuba 3 / Sionna RT trace.

    Construction validates that the GPU stack is importable and fails
    loud otherwise — so a misconfigured ``$SIONNA_RT_BACKEND=sionna_rt``
    is caught at boot, not mid-trace.

    Tijolo 8: ``.trace()`` now wires Mitsuba ``load_file`` + Sionna RT
    ``PathSolver``. The actual GPU validation happens once ops rolls
    a CUDA-capable instance — until then this code path is exercised
    via mocked ``mitsuba`` / ``sionna_rt`` modules in tests.
    """

    name = "sionna_rt"

    def __init__(self) -> None:
        missing = []
        try:
            import mitsuba  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            missing.append("mitsuba")
        # Sionna RT 2.x PyPI wheel ships as ``sionna.rt`` (sub-module),
        # but earlier 1.x and some redistributions exposed ``sionna_rt``
        # at the top level.  Accept either — only flag missing when
        # neither importable.
        try:
            import sionna.rt  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            try:
                import sionna_rt  # type: ignore[import-not-found]  # noqa: F401
            except ImportError:
                missing.append("sionna.rt")
        if missing:
            raise RuntimeError(
                "sionna_rt tracer selected but required modules are not "
                f"installed: {missing}. Install mitsuba>=3.5 and "
                "sionna-rt>=2.0 on a CUDA-capable host, or set "
                "SIONNA_RT_BACKEND=fspl_stub."
            )

    def trace(self, scene_dir: str, job: "Job") -> "Any":
        import math
        import mitsuba as mi  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        try:
            import sionna.rt as srt  # type: ignore[import-not-found]
        except ImportError:
            import sionna_rt as srt  # type: ignore[import-not-found]

        manifest = _load_manifest(scene_dir)
        scene_xml = _resolve_scene_xml(scene_dir)

        # Mitsuba variant: pick the best one available; ops may pin via
        # $MITSUBA_VARIANT (e.g. force CPU on a GPU box for repro work).
        variant = _select_mitsuba_variant(mi)
        mi.set_variant(variant)

        # Local-frame origin matches the scene-builder (mitsuba_scene.py:
        # bbox centroid, equirectangular projection). Re-derive here so
        # the worker stays decoupled from that module.
        bbox = manifest.get("bbox") or [
            job.bbox_south, job.bbox_west, job.bbox_north, job.bbox_east,
        ]
        m_south, m_west, m_north, m_east = (float(b) for b in bbox)
        lon0 = (m_west + m_east) / 2.0
        lat0 = (m_south + m_north) / 2.0
        R = 6_371_008.8
        cos_lat0 = math.cos(math.radians(lat0))

        def _proj(lat: float, lon: float) -> tuple[float, float]:
            x = math.radians(lon - lon0) * R * cos_lat0
            y = math.radians(lat - lat0) * R
            return x, y

        scene = srt.load_scene(scene_xml)
        scene.frequency = float(job.frequency_hz)
        # Isotropic single-element TX/RX arrays: per-pixel coverage map
        # values are antenna-independent path gain.
        scene.tx_array = srt.PlanarArray(
            num_rows=1, num_cols=1, pattern="iso", polarization="V",
        )
        scene.rx_array = srt.PlanarArray(
            num_rows=1, num_cols=1, pattern="iso", polarization="V",
        )

        tx_x, tx_y = _proj(job.tx_lat, job.tx_lon)
        scene.add(srt.Transmitter(
            name="tx", position=[tx_x, tx_y, float(job.tx_height_m)],
        ))

        # Receiver-grid coverage map over the requested raster bbox.
        west_x, south_y = _proj(job.bbox_south, job.bbox_west)
        east_x, north_y = _proj(job.bbox_north, job.bbox_east)
        centre_x = (west_x + east_x) / 2.0
        centre_y = (south_y + north_y) / 2.0
        width = east_x - west_x
        height = north_y - south_y
        cell_w = width / float(job.cols)
        cell_h = height / float(job.rows)

        max_depth = int(os.getenv("SIONNA_RT_MAX_DEPTH", "5"))
        samples = int(os.getenv("SIONNA_RT_SAMPLES", "1000000"))
        rx_height = float(os.getenv("SIONNA_RT_RX_HEIGHT_M", "1.5"))

        solver = srt.PathSolver()
        cm = solver(
            scene=scene,
            max_depth=max_depth,
            samples_per_tx=samples,
            cell_size=(cell_w, cell_h),
            center=[centre_x, centre_y, rx_height],
            orientation=[0.0, 0.0, 0.0],
            size=[width, height],
        )

        # path_gain: linear gain per cell. Convert to basic loss in dB.
        path_gain = np.asarray(cm.path_gain).reshape(job.rows, job.cols)
        path_gain = np.maximum(path_gain, 1e-30)  # avoid log(0)
        loss_db = (-10.0 * np.log10(path_gain)).astype("float32")
        return loss_db


# ── Tracer helpers ───────────────────────────────────────────────

def _load_manifest(scene_dir: str) -> dict:
    """Read ``manifest.json`` from ``scene_dir``.

    Raises ``FileNotFoundError`` if missing, ``ValueError`` if malformed.
    The manifest is the contract between the scene-builder and the
    tracer; refusing to proceed without it prevents silent local-frame
    drift between TX placement and the receiver grid.
    """
    path = os.path.join(scene_dir, "manifest.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"manifest.json missing under {scene_dir}")
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as ex:
        raise ValueError(f"manifest.json malformed: {ex}") from ex


def _resolve_scene_xml(scene_dir: str) -> str:
    """Return the absolute path to ``scene.xml`` inside ``scene_dir``."""
    path = os.path.join(scene_dir, "scene.xml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"scene.xml missing under {scene_dir}")
    return path


# Preference order matches mitsuba's own recommendation: GPU > CPU JIT
# > scalar (last-resort, much slower). ``scalar_rgb`` is included so
# laptop/CI runs that lack LLVM still work.
_MITSUBA_VARIANT_PREFERENCE = (
    "cuda_ad_rgb",
    "llvm_ad_rgb",
    "scalar_rgb",
)


def _select_mitsuba_variant(mi) -> str:
    """Pick the best available Mitsuba variant.

    Honours ``$MITSUBA_VARIANT`` if set (must be in
    ``mi.variants()``). Otherwise picks the first preference that's
    available. Raises ``RuntimeError`` when none of the preferred
    variants are compiled into the local Mitsuba — better to fail at
    boot than to dispatch a job that will explode later.
    """
    available = list(mi.variants())
    pinned = os.getenv("MITSUBA_VARIANT")
    if pinned:
        if pinned not in available:
            raise RuntimeError(
                f"$MITSUBA_VARIANT={pinned!r} is not available in this "
                f"Mitsuba build. Available: {available}"
            )
        return pinned
    for v in _MITSUBA_VARIANT_PREFERENCE:
        if v in available:
            return v
    raise RuntimeError(
        "no supported Mitsuba variant available. "
        f"Wanted any of {list(_MITSUBA_VARIANT_PREFERENCE)}, "
        f"got {available}"
    )


_TRACERS = {
    _FsplStubTracer.name: _FsplStubTracer,
    _SionnaRtTracer.name: _SionnaRtTracer,
}


def select_tracer(name: Optional[str] = None):
    """Return an ``RFTracer`` instance for ``name`` (or ``$SIONNA_RT_BACKEND``).

    Raises ``ValueError`` on an unknown backend so a typo in env
    config fails at boot rather than after a job has already been
    dequeued.
    """
    if name is None:
        name = os.getenv("SIONNA_RT_BACKEND", "fspl_stub")
    cls = _TRACERS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown tracer backend: {name!r}. "
            f"Known: {sorted(_TRACERS)}"
        )
    return cls()


# ── Raster computation ───────────────────────────────────────────

def compute_raster_loss(scene_dir: str, job: Job) -> "Any":
    """Run the per-tile RF trace and return a ``(rows, cols)`` ndarray of dB loss.

    Backend is selected by :func:`select_tracer` (env
    ``$SIONNA_RT_BACKEND``, default ``fspl_stub``). The signature is
    stable across backends: T8 swaps ``sionna_rt``'s body in without
    touching this call site or its callers.
    """
    tracer = select_tracer()
    return tracer.trace(scene_dir, job)


def write_raster_npz(arr, job: Job, path: str) -> None:
    import numpy as np  # type: ignore[import-not-found]
    np.savez_compressed(
        path,
        loss_db=arr,
        bbox=np.asarray(
            [job.bbox_south, job.bbox_west, job.bbox_north, job.bbox_east],
            dtype="float64",
        ),
        frequency_hz=np.float64(job.frequency_hz),
        tx=np.asarray(
            [job.tx_lat, job.tx_lon, job.tx_height_m, job.tx_power_dbm],
            dtype="float64",
        ),
        job_id=np.asarray(job.job_id),
    )


# ── Lazy boto3 clients ───────────────────────────────────────────

_sqs_client = None
_s3_client = None


def _get_sqs():
    global _sqs_client
    if _sqs_client is None:
        import boto3  # type: ignore[import-not-found]
        _sqs_client = boto3.client(
            "sqs", region_name=os.getenv("AWS_REGION", "sa-east-1"),
        )
    return _sqs_client


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3  # type: ignore[import-not-found]
        _s3_client = boto3.client(
            "s3", region_name=os.getenv("AWS_REGION", "sa-east-1"),
        )
    return _s3_client


# ── Orchestration ────────────────────────────────────────────────

def process_message(
    msg: Mapping[str, Any],
    queue_url: str,
    *,
    sqs,
    s3,
    work_dir_root: Optional[str] = None,
) -> dict:
    """Process one SQS message end-to-end.

    Returns a status dict. On success, the message is deleted from
    SQS. Schema-invalid messages are deleted (poison-pill) with a
    logged error; transient failures (S3, manifest) leave the message
    in the queue for redrive.
    """
    receipt = msg["ReceiptHandle"]
    body = msg.get("Body", "")
    try:
        job = parse_job_message(body)
    except ValueError as ex:
        logger.error("rejecting poison-pill message: %s", ex)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        return {"status": "rejected", "reason": str(ex)}

    work_dir = tempfile.mkdtemp(prefix=f"rt-{job.job_id}-", dir=work_dir_root)
    try:
        logger.info("job %s: downloading %s", job.job_id, job.scene_s3_uri)
        download_scene_bundle(job.scene_s3_uri, work_dir, s3=s3)
        manifest_path = os.path.join(work_dir, "manifest.json")
        err = _validate_manifest(manifest_path)
        if err:
            logger.error("job %s: manifest invalid: %s", job.job_id, err)
            return {"status": "retry", "reason": err}

        logger.info(
            "job %s: tracing %dx%d @ %.2f GHz",
            job.job_id, job.rows, job.cols, job.frequency_hz / 1e9,
        )
        arr = compute_raster_loss(work_dir, job)

        out_path = os.path.join(work_dir, "raster.npz")
        write_raster_npz(arr, job, out_path)
        size = os.path.getsize(out_path)
        logger.info(
            "job %s: uploading %d bytes → %s",
            job.job_id, size, job.result_s3_uri,
        )
        upload_raster(out_path, job.result_s3_uri, s3=s3)

        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        return {"status": "ok", "job_id": job.job_id, "raster_bytes": size}
    except Exception as ex:
        logger.exception("job %s failed; leaving on queue for redrive", job.job_id)
        return {"status": "retry", "reason": f"{type(ex).__name__}: {ex}"}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def poll_loop(
    queue_url: str,
    *,
    sqs=None,
    s3=None,
    wait_seconds: int = _DEFAULT_WAIT_SECONDS,
    visibility_timeout: int = _DEFAULT_VISIBILITY,
    once: bool = False,
    idle_exit: bool = False,
    max_iterations: Optional[int] = None,
) -> list[dict]:
    """Long-poll SQS and process messages.

    Parameters
    ----------
    once: stop after the first message (test-friendly).
    idle_exit: stop after one empty receive (test-friendly).
    max_iterations: hard ceiling on receive-cycles (defence in depth).
    """
    sqs = sqs or _get_sqs()
    s3 = s3 or _get_s3()
    results: list[dict] = []
    i = 0
    while True:
        i += 1
        if max_iterations is not None and i > max_iterations:
            logger.info("poll_loop: max_iterations reached")
            break
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=visibility_timeout,
        )
        msgs: Iterable[Mapping[str, Any]] = resp.get("Messages", []) or []
        msgs = list(msgs)
        if not msgs:
            logger.debug("poll_loop: empty receive")
            if idle_exit:
                break
            continue
        for m in msgs:
            results.append(process_message(m, queue_url, sqs=sqs, s3=s3))
            if once:
                return results
    return results


# ── CLI ──────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Sionna RT GPU worker")
    p.add_argument("--probe", action="store_true",
                   help="print GPU stack versions and exit")
    p.add_argument("--poll", action="store_true",
                   help="enter the SQS polling loop")
    p.add_argument("--once", action="store_true",
                   help="(with --poll) process a single message and exit")
    p.add_argument("--idle-exit", action="store_true",
                   help="(with --poll) exit after one empty receive")
    p.add_argument("--queue-url", default=os.getenv("SIONNA_RT_QUEUE_URL", ""),
                   help="SQS queue URL (default $SIONNA_RT_QUEUE_URL)")
    args = p.parse_args(argv)

    gpu_info = _probe_gpu_stack()
    logger.info("gpu_stack=%s", json.dumps(gpu_info))

    if args.probe:
        sys.stdout.write(json.dumps(gpu_info, indent=2) + "\n")
        return 0

    if not args.poll:
        p.error("specify --probe or --poll")

    if os.getenv("SIONNA_RT_DISABLED", "1").lower() in {"1", "true", "yes"}:
        logger.error("SIONNA_RT_DISABLED is set; worker refuses to poll")
        return 3

    if not args.queue_url:
        logger.error("--queue-url / $SIONNA_RT_QUEUE_URL is required for --poll")
        return 4

    results = poll_loop(
        args.queue_url,
        once=args.once,
        idle_exit=args.idle_exit,
    )
    logger.info("poll_loop returned %d results", len(results))
    if any(r.get("status") == "retry" for r in results):
        return 5
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
