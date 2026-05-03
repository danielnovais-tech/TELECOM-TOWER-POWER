# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
"""Tests for the Planet Labs satellite-change robot.

These run without network access — `urllib.request.urlopen` is
stubbed so we exercise the filter-construction, response parsing,
flagging logic, and the no-api-key branch deterministically.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from scripts import satellite_change_robot as sat


def _fake_response(payload: dict) -> io.BytesIO:
    buf = io.BytesIO(json.dumps(payload).encode("utf-8"))
    # The context manager protocol is what urlopen returns.
    class _CM:
        def __enter__(self_inner):
            return buf
        def __exit__(self_inner, *a):
            buf.close()
            return False
    return _CM()


def test_bbox_around_is_symmetric_in_degrees():
    minx, miny, maxx, maxy = sat._bbox_around(0.0, 0.0, 111.0)
    # ~1° in each direction at the equator.
    assert abs((maxy - miny) - 2.0) < 0.01
    assert abs((maxx - minx) - 2.0) < 0.01


def test_filter_includes_aoi_date_and_cloud():
    bbox = sat._bbox_around(-15.8, -47.85, 1.0)
    f = sat._build_filter(bbox, "2026-04-01T00:00:00+00:00", 0.5)
    assert f["type"] == "AndFilter"
    field_names = {c["field_name"] for c in f["config"]}
    assert field_names == {"geometry", "acquired", "cloud_cover"}


def test_no_api_key_returns_no_api_key_error(tmp_path):
    sites_csv = tmp_path / "sites.csv"
    sites_csv.write_text("name,lat,lon\nT1,-15.8,-47.85\n")
    out = tmp_path / "rep.json"
    with patch.dict("os.environ", {}, clear=False) as env:
        env.pop("PLANET_API_KEY", None)
        rc = sat.main([
            "--sites-csv", str(sites_csv),
            "--output", str(out),
        ])
    assert rc == 0
    rep = json.loads(out.read_text())
    assert rep["api_key_present"] is False
    assert rep["sites"][0]["error"] == "no-api-key"
    assert rep["flagged_count"] == 0


def test_clear_scenes_flag_triggers_and_fail_on_flagged_exits_2(tmp_path):
    sites_csv = tmp_path / "sites.csv"
    sites_csv.write_text("name,lat,lon\nT1,-15.8,-47.85\n")
    out = tmp_path / "rep.json"
    fake_payload = {
        "features": [
            {"id": "PSS-A", "properties": {"cloud_cover": 0.05}},
            {"id": "PSS-B", "properties": {"cloud_cover": 0.30}},
            {"id": "PSS-C", "properties": {"cloud_cover": 0.02}},
        ],
    }
    with patch.dict("os.environ", {"PLANET_API_KEY": "test-key"}, clear=False), \
         patch.object(sat.urllib.request, "urlopen",
                      return_value=_fake_response(fake_payload)):
        rc = sat.main([
            "--sites-csv", str(sites_csv),
            "--output", str(out),
            "--fail-on-flagged",
        ])
    assert rc == 2  # flagged → non-zero per --fail-on-flagged
    rep = json.loads(out.read_text())
    site = rep["sites"][0]
    assert site["scenes_found"] == 3
    assert site["clear_scenes"] == 2  # 0.05 and 0.02 ≤ 0.1 default
    assert site["flagged"] is True
    assert site["sample_scene_id"] == "PSS-A"


def test_http_error_is_captured_per_site(tmp_path):
    import urllib.error
    sites_csv = tmp_path / "sites.csv"
    sites_csv.write_text("name,lat,lon\nT1,-15.8,-47.85\n")
    out = tmp_path / "rep.json"
    err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
    with patch.dict("os.environ", {"PLANET_API_KEY": "k"}, clear=False), \
         patch.object(sat.urllib.request, "urlopen", side_effect=err):
        rc = sat.main(["--sites-csv", str(sites_csv), "--output", str(out)])
    assert rc == 0  # robot tolerates per-site errors
    rep = json.loads(out.read_text())
    assert rep["sites"][0]["error"] == "http-429"
    assert rep["flagged_count"] == 0
