# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the site-placement GA.

Covers two regressions hit on 2026-05-02:
* The uncovered-receiver penalty must be discrete, not a floor-clip.
* `--aoi` is optional and is auto-derived from receivers when omitted.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from typing import List
from unittest.mock import patch

from scripts.optimize_sites import (
    AOI,
    Receiver,
    _UNCOVERED_PENALTY_DB,
    _evaluate_individual,
    main,
)


def _rx(lat: float, lon: float) -> Receiver:
    return Receiver(lat=lat, lon=lon, height_m=10.0, gain_db=0.0)


def _fake_loss(losses_by_pair):
    """Build a stub for `_link_loss_db` that returns scripted values."""
    def _stub(engine_name, f_hz, tx_lat, tx_lon, tx_h, rx):
        key = (round(tx_lat, 4), round(tx_lon, 4), round(rx.lat, 4), round(rx.lon, 4))
        return losses_by_pair.get(key)
    return _stub


def test_uncovered_penalty_is_discrete():
    """A receiver just above threshold must cost the full -UNCOV_PEN, not its raw margin.

    This is the regression that caused coverage to oscillate (84%→68%) while
    fitness climbed monotonically — the GA was happy to drop a receiver from
    150 dB if it bought >50 dB of slack on covered receivers.
    """
    rx_covered = _rx(-15.5, -47.5)
    rx_uncovered = _rx(-15.6, -47.6)
    tx = (-15.55, -47.55, 30.0)
    threshold_db = 145.0

    # rx_covered: loss=140  → margin=+5 clipped to MARGIN_CAP (3.0)
    # rx_uncovered: loss=150 → just above threshold; raw=−5
    losses = {
        (round(tx[0], 4), round(tx[1], 4), round(rx_covered.lat, 4), round(rx_covered.lon, 4)): 140.0,
        (round(tx[0], 4), round(tx[1], 4), round(rx_uncovered.lat, 4), round(rx_uncovered.lon, 4)): 150.0,
    }
    genome = [tx[0], tx[1], tx[2]]

    with patch("scripts.optimize_sites._link_loss_db", _fake_loss(losses)):
        fitness, cov, n_cov = _evaluate_individual(
            (genome, "itmlogic", 450e6, [rx_covered, rx_uncovered], threshold_db)
        )

    assert n_cov == 1
    assert cov == 0.5
    # Avg margin must be dominated by the discrete penalty, NOT raw -5.
    # ((+3) + (-200)) / 2 = -98.5  (minus tiny height pen ~0.15)
    assert fitness < -90.0, f"penalty not discrete: fitness={fitness}"


def test_uncovered_penalty_is_full_when_no_link():
    """When the engine returns None (no link at all), penalty is also -UNCOV_PEN."""
    rx = _rx(-15.5, -47.5)
    tx = (-15.55, -47.55, 30.0)
    with patch("scripts.optimize_sites._link_loss_db", lambda *a, **k: None):
        fitness, cov, n_cov = _evaluate_individual(
            ([tx[0], tx[1], tx[2]], "itmlogic", 450e6, [rx], 145.0)
        )
    assert n_cov == 0 and cov == 0.0
    assert fitness <= -_UNCOVERED_PENALTY_DB + 1.0


def _write_csv(tmp_dir: str, rows: List[tuple]) -> str:
    path = os.path.join(tmp_dir, "rx.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lat", "lon", "height", "gain"])
        for r in rows:
            w.writerow(r)
    return path


def test_cli_auto_aoi_from_receivers(monkeypatch):
    """Omitting --aoi derives an AOI from receivers' bbox + margin."""
    captured = {}

    def fake_run_ga(**kwargs):
        captured["aoi"] = kwargs["aoi"]
        towers = [(kwargs["aoi"].lat_min + 0.1, kwargs["aoi"].lon_min + 0.1, 30.0)]
        return towers, 0.0, {"champion_fitness": -1.0, "elapsed_seconds": 0.01, "history": []}

    with tempfile.TemporaryDirectory() as tmp:
        rx_csv = _write_csv(tmp, [(-15.0, -48.0, 10, 0), (-16.5, -46.5, 10, 0)])
        out_dir = os.path.join(tmp, "out")
        monkeypatch.setattr("scripts.optimize_sites.run_ga", fake_run_ga)
        monkeypatch.setattr("scripts.optimize_sites._render_map", lambda *a, **k: None)

        rc = main([
            "--receivers", rx_csv,
            "--n-towers", "1",
            "--engine", "itmlogic",
            "--generations", "1",
            "--pop", "2",
            "--workers", "1",
            "--aoi-margin-deg", "0.25",
            "--out", out_dir,
        ])

    assert rc == 0
    aoi = captured["aoi"]
    assert isinstance(aoi, AOI)
    # bbox = (-16.5,-48.0)..(-15.0,-46.5) padded by 0.25
    assert aoi.lat_min == -16.75
    assert aoi.lon_min == -48.25
    assert aoi.lat_max == -14.75
    assert aoi.lon_max == -46.25


def test_cli_explicit_aoi_wins_over_receivers(monkeypatch):
    captured = {}

    def fake_run_ga(**kwargs):
        captured["aoi"] = kwargs["aoi"]
        return [(kwargs["aoi"].lat_min, kwargs["aoi"].lon_min, 30.0)], 0.0, {
            "champion_fitness": 0.0, "elapsed_seconds": 0.0, "history": [],
        }

    with tempfile.TemporaryDirectory() as tmp:
        rx_csv = _write_csv(tmp, [(-15.0, -48.0, 10, 0)])
        monkeypatch.setattr("scripts.optimize_sites.run_ga", fake_run_ga)
        monkeypatch.setattr("scripts.optimize_sites._render_map", lambda *a, **k: None)
        rc = main([
            "--aoi=-20.0,-50.0,-10.0,-40.0",
            "--receivers", rx_csv,
            "--n-towers", "1",
            "--engine", "itmlogic",
            "--generations", "1",
            "--pop", "2",
            "--workers", "1",
            "--out", os.path.join(tmp, "out"),
        ])
    assert rc == 0
    aoi = captured["aoi"]
    assert (aoi.lat_min, aoi.lon_min, aoi.lat_max, aoi.lon_max) == (-20.0, -50.0, -10.0, -40.0)
