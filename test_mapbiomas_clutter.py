"""Tests for the MapBiomas clutter extractor.

These tests do NOT require the `rasterio` package or an actual GeoTIFF
on disk — they exercise:

* No-raster mode → every lookup returns ``None`` (extractor is a no-op).
* The pure encoding helpers (label + one-hot) behave as a linear model
  expects: known classes occupy distinct slots, unknowns route to
  "Other", and one-hot vectors always sum to 1.
* The Redis cache short-circuits raster reads when a value is already
  present.

Real raster reads are covered by an end-to-end integration test
gated on the `MAPBIOMAS_RASTER_PATH` env var, skipped in CI by default.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest

import mapbiomas_clutter as mb


@pytest.fixture(autouse=True)
def _reset_singleton():
    mb.reset_extractor()
    yield
    mb.reset_extractor()


# ---------------------------------------------------------------------------
# Pure encoding helpers
# ---------------------------------------------------------------------------

def test_clutter_class_to_label_known_codes():
    assert mb.clutter_class_to_label(3) == "Forest"
    assert mb.clutter_class_to_label(15) == "Pasture"
    assert mb.clutter_class_to_label(24) == "Urban"
    assert mb.clutter_class_to_label(33) == "Water"


def test_clutter_class_to_label_unknown_or_none_maps_to_other():
    assert mb.clutter_class_to_label(None) == "Other"
    assert mb.clutter_class_to_label(99) == "Other"
    assert mb.clutter_class_to_label(-1) == "Other"


def test_one_hot_known_class_has_single_one():
    vec = mb.clutter_class_to_onehot(3)  # Forest
    assert vec.shape == (mb.ONE_HOT_DIM,)
    assert vec.sum() == pytest.approx(1.0)
    # Forest is the first entry in _TOP10_CLUTTER_CLASSES
    assert vec[0] == 1.0


def test_one_hot_unknown_class_routes_to_other_slot():
    vec_unknown = mb.clutter_class_to_onehot(999)
    vec_none = mb.clutter_class_to_onehot(None)
    other_idx = mb.ONE_HOT_FEATURE_NAMES.index("clutter_other")
    assert vec_unknown[other_idx] == 1.0
    assert vec_none[other_idx] == 1.0
    # Both unknowns and Nones must collapse to the same one-hot — a
    # linear model relies on that signal being stable.
    np.testing.assert_array_equal(vec_unknown, vec_none)


def test_one_hot_dim_matches_feature_names():
    assert len(mb.ONE_HOT_FEATURE_NAMES) == mb.ONE_HOT_DIM
    # No duplicate column names
    assert len(set(mb.ONE_HOT_FEATURE_NAMES)) == mb.ONE_HOT_DIM


# ---------------------------------------------------------------------------
# No-raster mode (default in CI)
# ---------------------------------------------------------------------------

def test_no_raster_returns_none_and_does_not_raise(monkeypatch):
    monkeypatch.delenv("MAPBIOMAS_RASTER_PATH", raising=False)
    monkeypatch.setattr(mb, "MAPBIOMAS_RASTER_PATH", "")
    ext = mb.MapBiomasExtractor(redis_url="")  # disable Redis explicitly
    assert ext.get_clutter_class(-15.78, -47.93) is None
    # Out-of-range coords are gracefully None, not exceptions.
    assert ext.get_clutter_class(91.0, 0.0) is None
    assert ext.get_clutter_class(0.0, 181.0) is None


def test_missing_raster_path_disables_lookups(tmp_path):
    bogus = tmp_path / "does_not_exist.tif"
    ext = mb.MapBiomasExtractor(raster_path=str(bogus), redis_url="")
    assert ext.get_clutter_class(-15.78, -47.93) is None


# ---------------------------------------------------------------------------
# Cache behaviour with a fake raster dataset
# ---------------------------------------------------------------------------

class _FakeDataset:
    """Stand-in for a rasterio dataset. Records read calls so tests can
    assert that the cache short-circuits subsequent identical lookups."""

    def __init__(self, value: int, width: int = 1000, height: int = 1000) -> None:
        self.width = width
        self.height = height
        self.crs = "EPSG:4326"
        self.nodata = 255
        self._value = value
        self.read_calls = 0

    def index(self, lon: float, lat: float):
        # Map roughly: lat in [-90, 90] → row, lon in [-180, 180] → col.
        row = int((90.0 - lat) / 180.0 * self.height)
        col = int((lon + 180.0) / 360.0 * self.width)
        return row, col

    def read(self, band: int, window: Any = None):
        self.read_calls += 1
        return np.array([[self._value]], dtype=np.uint8)

    def close(self) -> None:
        pass


def test_lru_cache_short_circuits_raster_reads(monkeypatch):
    fake = _FakeDataset(value=15)  # 15 = Pasture
    ext = mb.MapBiomasExtractor(raster_path=None, redis_url="")
    # Inject the fake dataset directly so _open() short-circuits.
    ext._raster_path = "/fake.tif"
    ext._dataset = fake

    code1 = ext.get_clutter_class(-15.78, -47.93)
    code2 = ext.get_clutter_class(-15.78, -47.93)
    code3 = ext.get_clutter_class(-15.7800001, -47.9300001)  # rounds to same key

    assert code1 == 15
    assert code2 == 15
    assert code3 == 15
    # Raster should only have been hit once — the rest are LRU hits.
    assert fake.read_calls == 1


def test_nodata_value_returns_none(monkeypatch):
    fake = _FakeDataset(value=255)  # nodata
    ext = mb.MapBiomasExtractor(raster_path=None, redis_url="")
    ext._raster_path = "/fake.tif"
    ext._dataset = fake
    assert ext.get_clutter_class(-15.78, -47.93) is None


def test_get_extractor_singleton_is_idempotent(monkeypatch):
    monkeypatch.setattr(mb, "MAPBIOMAS_RASTER_PATH", "")
    monkeypatch.setattr(mb, "MAPBIOMAS_REDIS_URL", "")
    a = mb.get_extractor()
    b = mb.get_extractor()
    assert a is b
