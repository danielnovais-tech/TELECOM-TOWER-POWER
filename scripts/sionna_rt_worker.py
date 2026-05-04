# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""scripts/sionna_rt_worker.py — GPU worker SQS consumer (Q2/2026 scaffold).

This is the entrypoint baked into ``Dockerfile.gpu``. It will eventually
poll ``$SQS_QUEUE_URL`` for ``coverage:rt`` jobs, download the scene
bundle from S3, run the trace on GPU, and upload the per-pixel loss
raster back to S3.

Today it only:

1. Probes that the GPU stack is importable and CUDA is visible.
2. Validates the manifest schema produced by ``build_mitsuba_scene.py``.
3. Refuses to start polling unless ``SIONNA_RT_DISABLED=0`` AND a
   manifest with ``implementation_status='complete'`` is reachable.

That is enough to (a) catch a broken GPU image at container start
instead of mid-job, and (b) keep AWS Batch from silently chewing
through the queue while the scene builder is still a stub.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

logger = logging.getLogger("sionna_rt_worker")


def _probe_gpu_stack() -> dict:
    """Import torch/mitsuba/drjit/sionna and report versions + CUDA."""
    info = {}
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


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(
        description="Sionna RT GPU worker (Q2/2026 scaffold)"
    )
    p.add_argument("--poll", action="store_true",
                   help="enter the SQS polling loop (currently a no-op stub)")
    p.add_argument("--probe", action="store_true",
                   help="print GPU stack versions and exit")
    args = p.parse_args(argv)

    gpu_info = _probe_gpu_stack()
    logger.info("gpu_stack=%s", json.dumps(gpu_info))

    if args.probe:
        sys.stdout.write(json.dumps(gpu_info, indent=2) + "\n")
        return 0

    if os.getenv("SIONNA_RT_DISABLED", "1").lower() in {"1", "true", "yes"}:
        logger.error("SIONNA_RT_DISABLED is set; worker refuses to poll")
        return 3

    scene_path = os.getenv("SIONNA_RT_SCENE_PATH", "")
    manifest_path = os.path.join(os.path.dirname(scene_path), "manifest.json") \
        if scene_path else ""
    err = _validate_manifest(manifest_path) if manifest_path else \
        "SIONNA_RT_SCENE_PATH unset"
    if err:
        logger.error("manifest validation failed: %s", err)
        return 4

    # Polling loop is intentionally not implemented yet — see roadmap.
    logger.warning(
        "polling loop is a Q2/2026 scaffold; sleeping forever so AWS Batch "
        "doesn't restart-storm. Set --probe to inspect the GPU stack."
    )
    while True:  # pragma: no cover
        time.sleep(3600)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
