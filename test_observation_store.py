# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for observation_store.py – SQLite backend, no DATABASE_URL."""

import os
import pytest

# Force SQLite backend regardless of caller env
os.environ.pop("DATABASE_URL", None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = tmp_path / "obs.db"
    monkeypatch.setenv("TOWER_DB_PATH", str(db))
    # Reload module so it picks up the patched env
    import importlib
    import observation_store as os_mod
    importlib.reload(os_mod)
    return os_mod.ObservationStore()


def _sample_obs(**overrides):
    base = {
        "tower_id": "T-1",
        "tx_lat": -23.5, "tx_lon": -46.6,
        "tx_height_m": 30.0, "tx_power_dbm": 43.0, "tx_gain_dbi": 17.0,
        "rx_lat": -23.51, "rx_lon": -46.59,
        "rx_height_m": 1.5, "rx_gain_dbi": 0.0,
        "freq_hz": 1.8e9, "observed_dbm": -85.0,
        "source": "unit-test",
    }
    base.update(overrides)
    return base


def test_insert_and_iter_observations(store):
    store.insert_observation(_sample_obs())
    store.insert_observation(_sample_obs(tower_id="T-2", observed_dbm=-72.0))
    rows = list(store.iter_observations())
    assert len(rows) == 2
    assert {r["tower_id"] for r in rows} == {"T-1", "T-2"}
    assert {-85.0, -72.0} == {r["observed_dbm"] for r in rows}


def test_insert_observations_many(store):
    rows = [_sample_obs(tower_id=f"T-{i}", observed_dbm=-70 - i) for i in range(5)]
    store.insert_observations_many(rows)
    assert store.counts()["link_observations"] == 5


# NOTE: tests for upsert_cell_samples_many / iter_cell_samples were removed
# alongside the OpenCelliD averageSignal training path. The free-tier feed
# returns 0 for 100 % of Brazilian rows, so the upsert path is unreachable
# in practice. The schema + methods remain for backwards-compat with any
# pre-existing prod data; if you need to revive it, restore both the test
# and the call sites in coverage_predict.load_historical_from_stores.


def test_counts_starts_empty(store):
    c = store.counts()
    assert c["link_observations"] == 0
    assert c["cell_signal_samples"] == 0
