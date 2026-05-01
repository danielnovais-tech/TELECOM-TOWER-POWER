# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for snap_anatel.py — the geocoding-precision pass that snaps each
ANATEL tower to its nearest same-operator OpenCelliD tower within 5 km.
"""
from __future__ import annotations

import math
from typing import List, Dict, Any

import snap_anatel


def _t(id_: str, lat: float, lon: float, op: str) -> Dict[str, Any]:
    return {"id": id_, "lat": lat, "lon": lon, "operator": op,
            "height_m": 35.0, "bands": ["700MHz"], "power_dbm": 43.0}


def test_find_nearest_same_operator_within_radius():
    ocid = [
        _t("OCID_1", -23.5500, -46.6300, "Vivo"),    # ~near São Paulo
        _t("OCID_2", -23.5600, -46.6400, "Claro"),   # different operator
        _t("OCID_3", -23.5510, -46.6310, "Vivo"),    # closer same operator
    ]
    idx = snap_anatel.build_index(ocid)
    match = snap_anatel.find_nearest(-23.5505, -46.6305, "Vivo", idx, max_km=5.0)
    assert match is not None
    cand, dkm = match
    assert cand["id"] == "OCID_3"
    assert dkm < 0.2  # < 200 m


def test_find_nearest_skips_other_operators():
    ocid = [_t("OCID_1", -23.5500, -46.6300, "Claro")]
    idx = snap_anatel.build_index(ocid)
    assert snap_anatel.find_nearest(-23.5505, -46.6305, "Vivo", idx, max_km=5.0) is None


def test_find_nearest_respects_max_km():
    # OCID tower 50 km away — must NOT be returned with default 5 km
    ocid = [_t("OCID_FAR", -24.0, -47.0, "Vivo")]
    idx = snap_anatel.build_index(ocid)
    assert snap_anatel.find_nearest(-23.55, -46.63, "Vivo", idx, max_km=5.0) is None


class _StubStore:
    """Minimal TowerStore substitute for snap_anatel testing."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.backend = "memory"
        self._rows = list(rows)
        self.written: List[Dict[str, Any]] = []

    def list_all(self, operator=None, limit=10_000_000, offset=0, owner=None):
        return list(self._rows)

    def upsert_many(self, rows):
        self.written.extend(rows)
        return len(rows)


def test_snap_anatel_end_to_end_dry_run_does_not_write():
    rows = [
        _t("ANATEL_1", -23.5505, -46.6305, "Vivo"),   # noisy centroid
        _t("OCID_1",  -23.5510, -46.6310, "Vivo"),    # ground truth ~70 m away
    ]
    store = _StubStore(rows)
    stats = snap_anatel.snap_anatel(max_km=5.0, dry_run=True, store=store)
    assert stats["anatel"] == 1
    assert stats["ocid"] == 1
    assert stats["snapped"] == 1
    assert 0 < stats["median_m"] < 200
    assert store.written == []


def test_snap_anatel_writes_in_place_when_not_dry_run():
    rows = [
        _t("ANATEL_1", -23.5505, -46.6305, "Vivo"),
        _t("OCID_1",  -23.5510, -46.6310, "Vivo"),
    ]
    store = _StubStore(rows)
    stats = snap_anatel.snap_anatel(max_km=5.0, dry_run=False, store=store)
    assert stats["snapped"] == 1
    assert len(store.written) == 1
    written = store.written[0]
    # ID is preserved (in-place update), lat/lon now match the OCID candidate.
    assert written["id"] == "ANATEL_1"
    assert math.isclose(written["lat"], -23.5510, abs_tol=1e-7)
    assert math.isclose(written["lon"], -46.6310, abs_tol=1e-7)


def test_snap_anatel_no_match_when_no_same_operator_ocid():
    rows = [
        _t("ANATEL_1", -23.5505, -46.6305, "Vivo"),
        _t("OCID_1",  -23.5510, -46.6310, "Claro"),  # different operator
    ]
    store = _StubStore(rows)
    stats = snap_anatel.snap_anatel(max_km=5.0, dry_run=False, store=store)
    assert stats["snapped"] == 0
    assert store.written == []
