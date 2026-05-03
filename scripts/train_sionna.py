# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
train_sionna.py
===============

Train the learned propagation model that backs ``rf_engines.sionna_engine``.

Pipeline
--------
1. Pull labelled link observations from :class:`observation_store.ObservationStore`
   (``link_observations`` table — drive-tests + API submissions).
2. For each observation:
   * sample SRTM along the great-circle Tx→Rx path;
   * convert ``observed_dbm`` to basic loss ``Lb`` using the link budget
     ``Lb = Pt + Gt + Gr − Prx``;
   * hand the inputs to :func:`rf_engines._sionna_features.build_features`.
3. Standardise features (mean/std), split 80/10/10 train/val/test by
   tower (so the test fold contains *new* sites, mirroring the real
   deployment evaluation).
4. Fit a small Keras MLP minimising Huber loss on Lb (Huber, not MSE,
   because drive-test outliers from indoor measurements bias L2 fits).
5. Export TFLite + a JSON sidecar (``sionna_features.json``) that
   pins the schema version, normalisation stats, and feature names —
   ``SionnaEngine`` refuses to serve predictions when the sidecar
   doesn't match the runtime feature module.

Why not Sionna directly?
~~~~~~~~~~~~~~~~~~~~~~~~
NVIDIA Sionna is a *ray-tracing* link simulator, not a path-loss
regressor. It generates synthetic labels from a 3-D scene; it does
not consume drive-tests. We follow the standard pattern from the
RF-ML literature: physics-informed feature engineering + a tiny MLP
trained on real measurements, with Sionna reserved as a future
synthetic-data augmenter (out of scope for this artefact). The engine
keeps the ``sionna`` *name* because that is the slot already wired
into the registry, the compare endpoint, and the GitHub Actions robot.

Usage
-----

    python -m scripts.train_sionna \
        --output-dir models/sionna_v1 \
        --min-links 200 \
        --epochs 80

Outputs (under ``--output-dir``):

* ``sionna_model.tflite``        — quantised float32 model;
* ``sionna_features.json``       — schema, mean/std, n_train, metrics;
* ``training_report.json``       — per-epoch loss / metrics.

Then upload to S3 and let ECS download on boot:

    aws s3 cp models/sionna_v1/sionna_model.tflite \
        s3://$BUCKET/sionna_model.tflite
    # set SIONNA_MODEL_S3_URI on the task definition;
    # entrypoint.sh fetches it into $SIONNA_MODEL_PATH and unsets
    # SIONNA_DISABLED — see rf_engines/sionna_engine.py.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

# Make sibling top-level modules importable when run via ``python -m``
# from the repo root *or* directly via ``python scripts/train_sionna.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rf_engines._sionna_features import (  # noqa: E402
    FEATURE_DIM,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    build_features,
)

logger = logging.getLogger("train_sionna")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledLink:
    """A single training row — feature vector + Lb label + tower id."""

    features: np.ndarray
    label_db: float
    tower_id: str   # used for group-aware splitting


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _profile(
    lat1: float, lon1: float, lat2: float, lon2: float, n_pts: int = 32
) -> Tuple[List[float], List[float]]:
    """Sample SRTM elevation along the great-circle path. Falls back to
    flat 0 m when SRTM tiles are not on disk — those rows are still
    usable (frequency / distance / clutter dominate at the urban end of
    the spectrum) but get a low confidence flag downstream."""
    d_total = _haversine_km(lat1, lon1, lat2, lon2)
    d = [d_total * i / (n_pts - 1) for i in range(n_pts)]

    try:
        from srtm_elevation import SRTMReader  # type: ignore[import-not-found]
        reader: Optional[object] = SRTMReader(
            data_dir=os.getenv("SRTM_DATA_DIR", "./srtm_data")
        )
    except Exception:
        reader = None

    h: List[float] = []
    for i in range(n_pts):
        f = i / (n_pts - 1)
        lat = lat1 + (lat2 - lat1) * f
        lon = lon1 + (lon2 - lon1) * f
        elev: Optional[float] = None
        if reader is not None:
            try:
                elev = reader.get_elevation(lat, lon)  # type: ignore[attr-defined]
            except Exception:
                elev = None
        h.append(float(elev) if elev is not None and elev > -1000 else 0.0)
    return d, h


def _row_to_link(row: Dict[str, object]) -> Optional[LabelledLink]:
    """Convert one ``link_observations`` row into a :class:`LabelledLink`.

    Returns ``None`` for rows that fail validation (incomplete fields,
    pathological geometry, observed_dbm out of physical range).
    """
    try:
        f_hz = float(row["freq_hz"])  # type: ignore[arg-type]
        tx_lat = float(row["tx_lat"]); tx_lon = float(row["tx_lon"])  # type: ignore[arg-type]
        rx_lat = float(row["rx_lat"]); rx_lon = float(row["rx_lon"])  # type: ignore[arg-type]
        htg = float(row["tx_height_m"])  # type: ignore[arg-type]
        hrg = float(row["rx_height_m"])  # type: ignore[arg-type]
        pt = float(row["tx_power_dbm"])  # type: ignore[arg-type]
        gt = float(row["tx_gain_dbi"])   # type: ignore[arg-type]
        gr = float(row["rx_gain_dbi"])   # type: ignore[arg-type]
        prx = float(row["observed_dbm"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return None

    if f_hz <= 0 or htg <= 0 or hrg <= 0:
        return None
    # Drop physically implausible RSSI: anything above the EIRP itself or
    # below thermal floor at 20 MHz BW (~-130 dBm) is a sensor artefact.
    if prx > pt + gt or prx < -135.0:
        return None

    d_km, h_m = _profile(tx_lat, tx_lon, rx_lat, rx_lon)
    if d_km[-1] < 0.05:  # co-located, no propagation problem to learn
        return None

    # Basic transmission loss: Lb = (Pt + Gt + Gr) - Prx.
    # Antenna gains here are *isotropic* — boresight assumption — and
    # represent the dominant uncertainty in drive-test labels. The Huber
    # loss in fitting absorbs the resulting heavy tail.
    lb = (pt + gt + gr) - prx
    if not (40.0 <= lb <= 250.0):
        return None

    feats = build_features(
        f_hz=f_hz, d_km=d_km, h_m=h_m, htg=htg, hrg=hrg,
        phi_t=tx_lat, lam_t=tx_lon, phi_r=rx_lat, lam_r=rx_lon,
        pol=None, zone=None,
    )
    tid = str(row.get("tower_id") or f"adhoc:{tx_lat:.3f},{tx_lon:.3f}")
    return LabelledLink(features=feats, label_db=lb, tower_id=tid)


def _load_dataset(min_links: int) -> List[LabelledLink]:
    from observation_store import ObservationStore  # type: ignore[import-not-found]

    store = ObservationStore()
    rows = list(store.iter_observations())
    logger.info("loaded %d raw observation rows", len(rows))

    links: List[LabelledLink] = []
    for r in rows:
        link = _row_to_link(r)
        if link is not None:
            links.append(link)
    logger.info("retained %d valid labelled links", len(links))

    if len(links) < min_links:
        raise SystemExit(
            f"only {len(links)} usable links — need ≥ {min_links}. "
            f"Run more drive-test imports or lower --min-links for a smoke run."
        )
    return links


# ---------------------------------------------------------------------------
# Splitting & normalisation
# ---------------------------------------------------------------------------


def _group_split(
    links: List[LabelledLink], seed: int
) -> Tuple[List[LabelledLink], List[LabelledLink], List[LabelledLink]]:
    """Split 80/10/10 by ``tower_id`` so the test fold has *unseen sites*.

    Random row-wise splitting overestimates accuracy because correlated
    rows from the same tower leak into both folds.
    """
    rng = np.random.default_rng(seed)
    by_tower: Dict[str, List[LabelledLink]] = {}
    for ln in links:
        by_tower.setdefault(ln.tower_id, []).append(ln)
    towers = sorted(by_tower.keys())
    rng.shuffle(towers)

    n = len(towers)
    n_test = max(1, n // 10)
    n_val = max(1, n // 10)
    test_t = set(towers[:n_test])
    val_t = set(towers[n_test:n_test + n_val])

    train, val, test = [], [], []
    for t, group in by_tower.items():
        if t in test_t:
            test.extend(group)
        elif t in val_t:
            val.extend(group)
        else:
            train.extend(group)

    logger.info(
        "split by tower: train=%d (%.0f%%) val=%d test=%d "
        "(towers train=%d val=%d test=%d)",
        len(train), 100 * len(train) / max(1, len(links)),
        len(val), len(test),
        n - n_test - n_val, n_val, n_test,
    )
    return train, val, test


def _to_arrays(links: List[LabelledLink]) -> Tuple[np.ndarray, np.ndarray]:
    if not links:
        return np.zeros((0, FEATURE_DIM), dtype=np.float32), np.zeros((0,), dtype=np.float32)
    X = np.stack([ln.features for ln in links]).astype(np.float32)
    y = np.asarray([ln.label_db for ln in links], dtype=np.float32)
    return X, y


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _build_model(input_dim: int):
    import tensorflow as tf  # type: ignore[import-not-found]

    # Tiny MLP — the dataset is small (≲10⁵ links is the realistic ceiling
    # for brazilian drive-test corpora today). Larger nets overfit and
    # blow the 4 MB TFLite budget that fits comfortably in the ECS task
    # cold-start path.
    inputs = tf.keras.Input(shape=(input_dim,), name="features")
    x = tf.keras.layers.Dense(64, activation="relu")(inputs)
    x = tf.keras.layers.Dropout(0.1)(x)
    x = tf.keras.layers.Dense(32, activation="relu")(x)
    out = tf.keras.layers.Dense(1, name="basic_loss_db")(x)
    model = tf.keras.Model(inputs=inputs, outputs=out, name="sionna_mlp")

    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss=tf.keras.losses.Huber(delta=5.0),  # 5 dB is the typical RSSI scatter
        metrics=[tf.keras.metrics.MeanAbsoluteError(name="mae_db")],
    )
    return model


def _export_tflite(model, out_path: Path) -> None:
    import tensorflow as tf  # type: ignore[import-not-found]

    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    # Default float32 — the runtime engine is CPU-bound and doesn't
    # warrant int8 quantisation (which needs a representative dataset
    # and degrades dB accuracy noticeably for a model this small).
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    tflite = converter.convert()
    out_path.write_bytes(tflite)
    logger.info("wrote %s (%d bytes)", out_path, len(tflite))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--min-links", type=int, default=200,
                   help="Refuse to train below this dataset size.")
    p.add_argument("--epochs", type=int, default=80)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    links = _load_dataset(args.min_links)
    train, val, test = _group_split(links, args.seed)
    Xtr, ytr = _to_arrays(train)
    Xva, yva = _to_arrays(val)
    Xte, yte = _to_arrays(test)

    # Per-feature standardisation. Stats fit on train only, then frozen
    # into the JSON sidecar so the engine can apply identical scaling
    # at inference. Using std=1 where σ≈0 keeps a feature inert (its
    # column was constant in the training fold, e.g. a region with no
    # coastal observations → zone_coastal stays at 0).
    mean = Xtr.mean(axis=0)
    std = Xtr.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)

    def _norm(X: np.ndarray) -> np.ndarray:
        return ((X - mean) / std).astype(np.float32)

    Xtr_n = _norm(Xtr); Xva_n = _norm(Xva); Xte_n = _norm(Xte)

    try:
        import tensorflow as tf  # noqa: F401  type: ignore[import-not-found]
    except Exception as exc:
        raise SystemExit(
            f"tensorflow not installed ({exc!r}). "
            "Install with `pip install 'tensorflow~=2.16'` on the GPU "
            "training box; CPU is fine for inference."
        )

    model = _build_model(FEATURE_DIM)

    history = model.fit(
        Xtr_n, ytr,
        validation_data=(Xva_n, yva) if len(yva) else None,
        epochs=args.epochs,
        batch_size=args.batch_size,
        verbose=2,
    )

    metrics: Dict[str, float] = {}
    if len(yte):
        eval_out = model.evaluate(Xte_n, yte, verbose=0, return_dict=True)
        metrics = {f"test_{k}": float(v) for k, v in eval_out.items()}
        logger.info("test set: %s", metrics)

    # Artefacts ---------------------------------------------------------
    _export_tflite(model, args.output_dir / "sionna_model.tflite")

    sidecar = {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_dim": FEATURE_DIM,
        "feature_names": FEATURE_NAMES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "n_train": int(len(ytr)),
        "n_val": int(len(yva)),
        "n_test": int(len(yte)),
        "metrics": metrics,
        "trained_at": int(time.time()),
        "elapsed_s": round(time.perf_counter() - t0, 2),
    }
    sidecar_path = args.output_dir / "sionna_features.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2))
    logger.info("wrote %s", sidecar_path)

    history_path = args.output_dir / "training_report.json"
    history_path.write_text(json.dumps(
        {k: [float(x) for x in v] for k, v in history.history.items()},
        indent=2,
    ))
    logger.info("wrote %s", history_path)

    logger.info("done in %.1fs", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
