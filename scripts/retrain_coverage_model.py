#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Retrain the coverage model when enough new ground-truth observations
have accumulated, then publish the new ``coverage_model.npz`` to S3.

Designed to be invoked from the ``retrain-coverage-model.yml`` GitHub
Actions workflow on a daily schedule. Idempotent: if the observation
delta since the last retrain is below ``--threshold`` the script exits
0 without uploading anything.

Usage::

    python -m scripts.retrain_coverage_model \\
        --s3-uri s3://telecom-tower-power-results/models/coverage_model.npz \\
        --threshold 1000

Environment:
    DATABASE_URL              Postgres connection string (read-only ok)
    BACKUP_AWS_*              AWS creds (set by the workflow via configure-aws-credentials)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from typing import Optional, Tuple

# Make the project root importable when invoked via ``python scripts/retrain_coverage_model.py``
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3  # noqa: E402

logger = logging.getLogger("retrain")


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise SystemExit(f"--s3-uri must start with s3:// (got {uri!r})")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise SystemExit(f"--s3-uri must be s3://bucket/key (got {uri!r})")
    return bucket, key


def _marker_key(model_key: str) -> str:
    """Marker lives next to the model: foo/coverage_model.npz → foo/coverage_model.last_retrain.json."""
    if model_key.endswith(".npz"):
        return model_key[: -len(".npz")] + ".last_retrain.json"
    return model_key + ".last_retrain.json"


def _read_marker(s3, bucket: str, key: str) -> dict:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return {}
    except Exception as e:
        # Treat any read error as "no prior marker" — we'd rather retrain
        # than silently skip when S3 is flaky.
        logger.warning("Could not read marker s3://%s/%s: %s", bucket, key, e)
        return {}


def _write_marker(s3, bucket: str, key: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def _count_observations() -> int:
    """Total real point-to-point measurements in ``link_observations``.

    These are submitted via ``POST /coverage/observations`` (auth required,
    pro tier+) and are the only **labelled** real data the model can learn
    from. The ``cell_signal_samples`` store is intentionally NOT counted
    here — the free-tier OpenCelliD feed reports ``averageSignal=0`` for
    all Brazilian rows, so those records carry no signal label even when
    present.
    """
    from observation_store import ObservationStore
    counts = ObservationStore().counts()
    return int(counts.get("link_observations", counts.get("observations", 0)))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--s3-uri", required=True,
                   help="Destination, e.g. s3://bucket/models/coverage_model.npz")
    p.add_argument("--threshold", type=int, default=1000,
                   help="Min new observations since last retrain (default: 1000)")
    p.add_argument("--n-synthetic", type=int, default=10_000,
                   help="Synthetic samples blended into training (default: 10000)")
    p.add_argument("--force", action="store_true",
                   help="Retrain even if delta < threshold.")
    p.add_argument("--dry-run", action="store_true",
                   help="Train and report metrics but do not upload to S3.")
    p.add_argument(
        "--bands-s3-prefix", default="",
        help=(
            "Optional S3 prefix to also publish per-band ridge artefacts to "
            "(e.g. s3://bucket/models/bands). When set, "
            "train_band_aware_model() runs after the global ridge and the "
            "resulting coverage_model_<MHz>.npz files + manifest.json are "
            "uploaded under this prefix."
        ),
    )
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    bucket, model_key = _parse_s3_uri(args.s3_uri)
    marker_key = _marker_key(model_key)
    s3 = boto3.client("s3")

    current = _count_observations()
    marker = _read_marker(s3, bucket, marker_key)
    last = int(marker.get("count", 0))
    delta = current - last
    logger.info("observation count: current=%d last_retrain=%d delta=%d threshold=%d",
                current, last, delta, args.threshold)

    if delta < args.threshold and not args.force:
        logger.info("delta below threshold; skipping retrain")
        # Surface the numbers in the workflow log + GH summary
        gh_out = os.getenv("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a") as f:
                f.write(f"retrained=false\nobservations={current}\ndelta={delta}\n")
        return 0

    # ------------------------------------------------------------------
    # Train (uses observation_store + synthetic samples)
    # ------------------------------------------------------------------
    from coverage_predict import (
        load_historical_from_stores, train_model, train_band_aware_model,
        MODEL_PATH,
    )

    historical = load_historical_from_stores(
        include_observations=True,
    )
    logger.info("loaded %d historical samples", len(historical))

    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "coverage_model.npz")
        model = train_model(
            n_synthetic=args.n_synthetic,
            historical=historical,
            save_to=out_path,
        )
        logger.info(
            "trained: rmse=%.2f dB cv_rmse=%.2f±%.2f dB (k=%d) n_train=%d version=%s",
            model.rmse_db, model.cv_rmse_db, model.cv_rmse_std_db,
            model.cv_folds, model.n_train, model.version,
        )
        if model.rmse_by_morphology:
            logger.info("per-morphology RMSE: %s", model.rmse_by_morphology)
        if model.rmse_by_band:
            logger.info("per-band RMSE: %s", model.rmse_by_band)

        if args.dry_run:
            logger.info("dry-run; not uploading")
            gh_out = os.getenv("GITHUB_OUTPUT")
            if gh_out:
                with open(gh_out, "a") as f:
                    f.write(
                        f"retrained=false\nobservations={current}\ndelta={delta}\n"
                        f"rmse_db={model.rmse_db:.4f}\nn_train={model.n_train}\n"
                        f"cv_rmse_db={model.cv_rmse_db:.4f}\n"
                        f"cv_rmse_std_db={model.cv_rmse_std_db:.4f}\n"
                        f"cv_folds={model.cv_folds}\n"
                    )
            return 0

        s3.upload_file(out_path, bucket, model_key,
                       ExtraArgs={"ContentType": "application/octet-stream"})
        logger.info("uploaded s3://%s/%s", bucket, model_key)

        # ── Optional: band-aware companion artefacts ───────────────────
        bands_uploaded = 0
        if args.bands_s3_prefix:
            if not args.bands_s3_prefix.startswith("s3://"):
                raise SystemExit(
                    f"--bands-s3-prefix must be s3:// (got {args.bands_s3_prefix!r})"
                )
            band_bucket, _, band_prefix = (
                args.bands_s3_prefix[len("s3://"):].partition("/")
            )
            band_prefix = band_prefix.rstrip("/")
            band_dir = os.path.join(tmp, "bands")
            ba = train_band_aware_model(
                n_synthetic=args.n_synthetic,
                historical=historical,
                save_to_dir=band_dir,
                train_global_fallback=True,
            )
            for fname in sorted(os.listdir(band_dir)):
                src = os.path.join(band_dir, fname)
                key = f"{band_prefix}/{fname}" if band_prefix else fname
                ctype = (
                    "application/json" if fname.endswith(".json")
                    else "application/octet-stream"
                )
                s3.upload_file(
                    src, band_bucket, key, ExtraArgs={"ContentType": ctype},
                )
                bands_uploaded += 1
            logger.info(
                "uploaded %d band artefacts to %s (%d band ridges, %s global)",
                bands_uploaded, args.bands_s3_prefix, len(ba.models),
                "with" if ba.global_model is not None else "no",
            )

    payload = {
        "count": current,
        "delta": delta,
        "trained_at": time.time(),
        "rmse_db": model.rmse_db,
        "n_train": model.n_train,
        "version": model.version,
        "cv_rmse_db": model.cv_rmse_db,
        "cv_rmse_std_db": model.cv_rmse_std_db,
        "cv_folds": model.cv_folds,
        "rmse_by_morphology": model.rmse_by_morphology,
        "rmse_by_band": model.rmse_by_band,
    }
    _write_marker(s3, bucket, marker_key, payload)
    logger.info("wrote marker s3://%s/%s", bucket, marker_key)

    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(
                f"retrained=true\nobservations={current}\ndelta={delta}\n"
                f"rmse_db={model.rmse_db:.4f}\nn_train={model.n_train}\n"
                f"cv_rmse_db={model.cv_rmse_db:.4f}\n"
                f"cv_rmse_std_db={model.cv_rmse_std_db:.4f}\n"
                f"cv_folds={model.cv_folds}\n"
                f"bands_uploaded={bands_uploaded}\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
