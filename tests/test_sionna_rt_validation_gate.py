# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""T10 — validation gate unit tests.

All tests run on CPU without any real Sionna RT / Mitsuba installation.
The engine registry is patched to inject synthetic engines whose
``predict_basic_loss`` returns controlled values, letting us test each
code path in ``scripts/sionna_rt_validation_gate``:

* ``rmse`` / ``mean`` helpers
* ``evaluate()`` — criterion A (sub-6 RMSE), criterion B (mmWave Δ)
* ``run_links()`` — routing through ``compare()``
* CLI entry-point — exit-0 on pass, exit-1 on fail, exit-2 when engine
  unavailable or unregistered
"""
from __future__ import annotations

import json
import math
import sys
import types
from pathlib import Path
from typing import Optional, Sequence
from unittest.mock import MagicMock, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sionna_rt_validation_gate import (  # noqa: E402
    _MMWAVE_DELTA_MIN_DB,
    _SUB6_RMSE_MAX_DB,
    evaluate,
    main,
    mean,
    rmse,
    run_links,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures and helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _sub6_row(rt: float, p1: float) -> dict:
    return {
        "link_index": 0, "band": "sub6", "f_hz": 900_000_000,
        "sionna_rt_loss_db": rt, "itu_p1812_loss_db": p1,
        "delta_db": rt - p1, "sionna_rt_available": True,
    }


def _mmwave_row(delta: float) -> dict:
    return {
        "link_index": 1, "band": "mmwave", "f_hz": 28_000_000_000,
        "sionna_rt_loss_db": 150.0, "itu_p1812_loss_db": 150.0 - delta,
        "delta_db": delta, "sionna_rt_available": True,
    }


def _unknown_band_row() -> dict:
    return {
        "link_index": 2, "band": "unknown", "f_hz": 5_000_000_000,
        "sionna_rt_loss_db": 110.0, "itu_p1812_loss_db": 110.0,
        "delta_db": 0.0, "sionna_rt_available": True,
    }


def _missing_row_sub6() -> dict:
    """Row where the RT engine returned None — should be skipped."""
    return {
        "link_index": 3, "band": "sub6", "f_hz": 900_000_000,
        "sionna_rt_loss_db": None, "itu_p1812_loss_db": 120.0,
        "delta_db": None, "sionna_rt_available": False,
    }


def _missing_row_mmwave() -> dict:
    return {
        "link_index": 4, "band": "mmwave", "f_hz": 28_000_000_000,
        "sionna_rt_loss_db": None, "itu_p1812_loss_db": None,
        "delta_db": None, "sionna_rt_available": False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — maths helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestRmse:
    def test_perfect_agreement(self):
        assert rmse([0.0, 0.0, 0.0]) == 0.0

    def test_symmetric_errors(self):
        # errors of +3 and -3 → RMSE = 3
        assert rmse([3.0, -3.0]) == pytest.approx(3.0)

    def test_typical_value(self):
        # [2, 4, 6, 8] → mean_sq = (4+16+36+64)/4 = 30 → √30 ≈ 5.477
        assert rmse([2.0, 4.0, 6.0, 8.0]) == pytest.approx(math.sqrt(30.0))

    def test_single_element(self):
        assert rmse([5.0]) == pytest.approx(5.0)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            rmse([])


class TestMean:
    def test_basic(self):
        assert mean([10.0, 20.0, 30.0]) == pytest.approx(20.0)

    def test_single(self):
        assert mean([7.5]) == pytest.approx(7.5)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            mean([])


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — evaluate()
# ═══════════════════════════════════════════════════════════════════════════════


class TestEvaluateCriterionA:
    """sub-6 GHz RMSE criterion (A)."""

    def test_pass_when_rmse_exactly_at_threshold(self):
        # RMSE = 6.0 exactly → must pass (≤)
        rows = [_sub6_row(126.0, 120.0)] * 1  # error = +6
        result = evaluate(rows, sub6_rmse_max=6.0, mmwave_delta_min=_MMWAVE_DELTA_MIN_DB)
        assert result["criterion_a"]["pass"] is True
        assert result["criterion_a"]["value"] == pytest.approx(6.0)

    def test_pass_when_rmse_below_threshold(self):
        rows = [_sub6_row(123.0, 120.0), _sub6_row(117.0, 120.0)]  # errors ±3 → RMSE=3
        result = evaluate(rows, sub6_rmse_max=6.0, mmwave_delta_min=_MMWAVE_DELTA_MIN_DB)
        assert result["criterion_a"]["pass"] is True
        assert result["criterion_a"]["value"] == pytest.approx(3.0)

    def test_fail_when_rmse_above_threshold(self):
        # single error of 7 dB → RMSE = 7
        rows = [_sub6_row(127.0, 120.0)]
        result = evaluate(rows, sub6_rmse_max=6.0, mmwave_delta_min=_MMWAVE_DELTA_MIN_DB)
        assert result["criterion_a"]["pass"] is False

    def test_skip_count_when_rt_none(self):
        rows = [_missing_row_sub6(), _sub6_row(122.0, 120.0)]
        result = evaluate(rows)
        assert result["criterion_a"]["links_evaluated"] == 1
        assert result["criterion_a"]["links_skipped"] == 1

    def test_none_when_no_sub6_links(self):
        rows = [_mmwave_row(15.0)]
        result = evaluate(rows)
        assert result["criterion_a"]["pass"] is None
        assert result["criterion_a"]["value"] is None
        assert result["criterion_a"]["links_evaluated"] == 0


class TestEvaluateCriterionB:
    """mmWave mean Δ criterion (B)."""

    def test_pass_when_delta_exceeds_threshold(self):
        rows = [_mmwave_row(12.0), _mmwave_row(15.0)]  # mean = 13.5 > 10
        result = evaluate(rows, sub6_rmse_max=_SUB6_RMSE_MAX_DB, mmwave_delta_min=10.0)
        assert result["criterion_b"]["pass"] is True
        assert result["criterion_b"]["value"] == pytest.approx(13.5)

    def test_fail_when_delta_equals_threshold(self):
        # > 10 strictly required; 10.0 exactly → fail
        rows = [_mmwave_row(10.0)]
        result = evaluate(rows, sub6_rmse_max=_SUB6_RMSE_MAX_DB, mmwave_delta_min=10.0)
        assert result["criterion_b"]["pass"] is False

    def test_fail_when_delta_below_threshold(self):
        rows = [_mmwave_row(5.0), _mmwave_row(8.0)]  # mean = 6.5 < 10
        result = evaluate(rows, sub6_rmse_max=_SUB6_RMSE_MAX_DB, mmwave_delta_min=10.0)
        assert result["criterion_b"]["pass"] is False

    def test_skip_count_when_delta_none(self):
        rows = [_missing_row_mmwave(), _mmwave_row(12.0)]
        result = evaluate(rows)
        assert result["criterion_b"]["links_evaluated"] == 1
        assert result["criterion_b"]["links_skipped"] == 1

    def test_none_when_no_mmwave_links(self):
        rows = [_sub6_row(122.0, 120.0)]
        result = evaluate(rows)
        assert result["criterion_b"]["pass"] is None
        assert result["criterion_b"]["value"] is None
        assert result["criterion_b"]["links_evaluated"] == 0


class TestEvaluateOverall:
    def test_overall_pass_when_both_pass(self):
        rows = [_sub6_row(122.0, 120.0), _mmwave_row(12.0)]
        result = evaluate(rows)
        assert result["overall_pass"] is True

    def test_overall_fail_when_criterion_a_fails(self):
        rows = [_sub6_row(130.0, 120.0), _mmwave_row(12.0)]  # RMSE=10 > 6
        result = evaluate(rows)
        assert result["overall_pass"] is False

    def test_overall_fail_when_criterion_b_fails(self):
        rows = [_sub6_row(122.0, 120.0), _mmwave_row(2.0)]  # Δ=2 < 10
        result = evaluate(rows)
        assert result["overall_pass"] is False

    def test_overall_fail_when_both_fail(self):
        rows = [_sub6_row(130.0, 120.0), _mmwave_row(2.0)]
        result = evaluate(rows)
        assert result["overall_pass"] is False

    def test_unknown_band_rows_are_ignored(self):
        """Rows with an unrecognised band don't affect either criterion."""
        rows = [_sub6_row(122.0, 120.0), _mmwave_row(12.0), _unknown_band_row()]
        result = evaluate(rows)
        assert result["overall_pass"] is True

    def test_only_sub6_links_passes_if_rmse_ok(self):
        """If the link set contains only sub-6 rows, criterion B is N/A (None)
        and overall_pass should be True when A passes."""
        rows = [_sub6_row(122.0, 120.0)]
        result = evaluate(rows)
        assert result["criterion_b"]["pass"] is None
        assert result["overall_pass"] is True

    def test_only_mmwave_links_passes_if_delta_ok(self):
        rows = [_mmwave_row(12.0)]
        result = evaluate(rows)
        assert result["criterion_a"]["pass"] is None
        assert result["overall_pass"] is True

    def test_no_links_at_all_is_overall_false(self):
        """Empty set — nothing evaluated → both None → overall False."""
        result = evaluate([])
        assert result["overall_pass"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — run_links() via mocked compare()
# ═══════════════════════════════════════════════════════════════════════════════

from rf_engines.compare import ComparisonResult, ComparisonRow  # noqa: E402


def _make_comparison_result(
    rt_loss: Optional[float],
    p1_loss: Optional[float],
    reference: str = "itu-p1812",
) -> ComparisonResult:
    """Build a minimal ComparisonResult as compare() would return."""
    p1_row = ComparisonRow(
        engine="itu-p1812",
        available=p1_loss is not None,
        basic_loss_db=p1_loss,
        confidence=1.0 if p1_loss is not None else None,
        runtime_ms=1.0,
        delta_db=0.0,
        extra={},
    )
    rt_delta = (rt_loss - p1_loss) if (rt_loss is not None and p1_loss is not None) else None
    rt_row = ComparisonRow(
        engine="sionna-rt",
        available=rt_loss is not None,
        basic_loss_db=rt_loss,
        confidence=1.0 if rt_loss is not None else None,
        runtime_ms=2.0 if rt_loss is not None else None,
        delta_db=rt_delta,
        extra={},
    )
    return ComparisonResult(reference=reference, rows=[p1_row, rt_row])


class TestRunLinks:
    def test_extracts_band_from_metadata(self):
        links = [
            {"_band": "sub6", "_comment": "x", "f_hz": 900_000_000,
             "d_km": [0.0, 1.0], "h_m": [10.0, 10.0],
             "htg": 30.0, "hrg": 1.5,
             "phi_t": -23.0, "lam_t": -46.0,
             "phi_r": -23.05, "lam_r": -46.05},
        ]
        with patch(
            "scripts.sionna_rt_validation_gate.compare",
            return_value=_make_comparison_result(125.0, 122.0),
        ):
            rows = run_links(links)
        assert len(rows) == 1
        row = rows[0]
        assert row["band"] == "sub6"
        assert row["sionna_rt_loss_db"] == pytest.approx(125.0)
        assert row["itu_p1812_loss_db"] == pytest.approx(122.0)
        assert row["delta_db"] == pytest.approx(3.0)

    def test_strips_underscore_keys_before_compare(self):
        """_band, _comment etc. must not be forwarded to compare()."""
        links = [{"_band": "mmwave", "_comment": "should be stripped",
                  "f_hz": 28_000_000_000, "d_km": [0.0, 0.1],
                  "h_m": [10.0, 10.0], "htg": 10.0, "hrg": 1.5,
                  "phi_t": -23.0, "lam_t": -46.0,
                  "phi_r": -23.01, "lam_r": -46.01}]

        captured_kwargs: dict = {}

        def _fake_compare(**kw):
            captured_kwargs.update(kw)
            return _make_comparison_result(160.0, 140.0)

        with patch("scripts.sionna_rt_validation_gate.compare", side_effect=_fake_compare):
            run_links(links)

        assert "_band" not in captured_kwargs
        assert "_comment" not in captured_kwargs
        assert "f_hz" in captured_kwargs

    def test_none_when_engine_unavailable(self):
        links = [{"_band": "sub6", "f_hz": 900_000_000,
                  "d_km": [0.0, 1.0], "h_m": [10.0, 10.0],
                  "htg": 30.0, "hrg": 1.5,
                  "phi_t": -23.0, "lam_t": -46.0,
                  "phi_r": -23.05, "lam_r": -46.05}]
        with patch(
            "scripts.sionna_rt_validation_gate.compare",
            return_value=_make_comparison_result(None, 120.0),
        ):
            rows = run_links(links)
        assert rows[0]["sionna_rt_loss_db"] is None
        assert rows[0]["sionna_rt_available"] is False

    def test_multiple_links_indexed_correctly(self):
        links = [
            {"_band": "sub6", "f_hz": 900_000_000,
             "d_km": [0.0, 1.0], "h_m": [10.0, 10.0],
             "htg": 30.0, "hrg": 1.5,
             "phi_t": -23.0, "lam_t": -46.0,
             "phi_r": -23.05, "lam_r": -46.05},
            {"_band": "mmwave", "f_hz": 28_000_000_000,
             "d_km": [0.0, 0.1], "h_m": [10.0, 10.0],
             "htg": 10.0, "hrg": 1.5,
             "phi_t": -23.0, "lam_t": -46.0,
             "phi_r": -23.01, "lam_r": -46.01},
        ]
        side_effects = [
            _make_comparison_result(123.0, 120.0),
            _make_comparison_result(160.0, 140.0),
        ]
        with patch("scripts.sionna_rt_validation_gate.compare", side_effect=side_effects):
            rows = run_links(links)
        assert rows[0]["link_index"] == 0
        assert rows[1]["link_index"] == 1
        assert rows[0]["band"] == "sub6"
        assert rows[1]["band"] == "mmwave"


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests — CLI / main()
# ═══════════════════════════════════════════════════════════════════════════════


def _make_fake_engine(name: str, *, available: bool, loss_db: Optional[float]):
    """Return a mock RFEngine-like object."""
    eng = MagicMock()
    eng.name = name
    eng.is_available.return_value = available
    from rf_engines.base import LossEstimate
    if loss_db is not None:
        eng.predict_basic_loss.return_value = LossEstimate(
            basic_loss_db=loss_db, engine=name, confidence=1.0
        )
    else:
        eng.predict_basic_loss.return_value = None
    return eng


class TestCLI:
    """End-to-end CLI tests patching the registry and compare()."""

    @pytest.fixture()
    def links_file(self, tmp_path):
        """Write a minimal two-link golden set."""
        data = [
            {"_band": "sub6", "f_hz": 900_000_000,
             "d_km": [0.0, 1.0], "h_m": [10.0, 10.0],
             "htg": 30.0, "hrg": 1.5,
             "phi_t": -23.0, "lam_t": -46.0,
             "phi_r": -23.05, "lam_r": -46.05},
            {"_band": "mmwave", "f_hz": 28_000_000_000,
             "d_km": [0.0, 0.1], "h_m": [10.0, 10.0],
             "htg": 10.0, "hrg": 1.5,
             "phi_t": -23.0, "lam_t": -46.0,
             "phi_r": -23.01, "lam_r": -46.01},
        ]
        p = tmp_path / "links.json"
        p.write_text(json.dumps(data))
        return p

    def _run(self, links_file, tmp_path, rt_loss, p1_loss, mmwave_rt=None, mmwave_p1=None,
             sub6_max=6.0, mmwave_min=10.0):
        """Helper: run main() with two patched compare() side effects."""
        out = tmp_path / "report.json"
        side1 = _make_comparison_result(rt_loss, p1_loss)
        side2 = _make_comparison_result(mmwave_rt or 160.0, mmwave_p1 or 140.0)

        rt_eng = _make_fake_engine("sionna-rt", available=True, loss_db=rt_loss)

        with (
            patch("scripts.sionna_rt_validation_gate.get_engine", return_value=rt_eng),
            patch("scripts.sionna_rt_validation_gate.compare", side_effect=[side1, side2]),
        ):
            code = main([
                "--links", str(links_file),
                "--output", str(out),
                "--sub6-rmse-db-max", str(sub6_max),
                "--mmwave-delta-db-min", str(mmwave_min),
            ])
        return code, out

    def test_exit_0_when_both_criteria_pass(self, links_file, tmp_path):
        """RMSE=2 (≤6), Δ=20 (>10) → exit 0."""
        code, out = self._run(
            links_file, tmp_path,
            rt_loss=122.0, p1_loss=120.0,  # sub6 error = +2 → RMSE = 2
            mmwave_rt=160.0, mmwave_p1=140.0,  # Δ = 20
        )
        assert code == 0
        report = json.loads(out.read_text())
        assert report["criteria"]["overall_pass"] is True

    def test_exit_1_when_criterion_a_fails(self, links_file, tmp_path):
        """RMSE=10 (>6) → exit 1."""
        code, _ = self._run(
            links_file, tmp_path,
            rt_loss=130.0, p1_loss=120.0,  # error = +10 → RMSE = 10
            mmwave_rt=160.0, mmwave_p1=140.0,
        )
        assert code == 1

    def test_exit_1_when_criterion_b_fails(self, links_file, tmp_path):
        """Δ=5 (<10) → exit 1."""
        code, _ = self._run(
            links_file, tmp_path,
            rt_loss=122.0, p1_loss=120.0,
            mmwave_rt=145.0, mmwave_p1=140.0,  # Δ = 5
        )
        assert code == 1

    def test_exit_1_when_both_criteria_fail(self, links_file, tmp_path):
        code, _ = self._run(
            links_file, tmp_path,
            rt_loss=130.0, p1_loss=120.0,
            mmwave_rt=142.0, mmwave_p1=140.0,  # Δ = 2
        )
        assert code == 1

    def test_report_file_written_on_pass(self, links_file, tmp_path):
        code, out = self._run(
            links_file, tmp_path,
            rt_loss=122.0, p1_loss=120.0,
            mmwave_rt=160.0, mmwave_p1=140.0,
        )
        assert out.exists()
        report = json.loads(out.read_text())
        assert "link_rows" in report
        assert report["gate"] == "sionna-rt"

    def test_report_file_written_on_fail(self, links_file, tmp_path):
        code, out = self._run(
            links_file, tmp_path,
            rt_loss=130.0, p1_loss=120.0,
            mmwave_rt=141.0, mmwave_p1=140.0,
        )
        assert out.exists()

    def test_exit_2_when_engine_not_available(self, links_file, tmp_path):
        out = tmp_path / "report.json"
        rt_eng = _make_fake_engine("sionna-rt", available=False, loss_db=None)
        with patch("scripts.sionna_rt_validation_gate.get_engine", return_value=rt_eng):
            code = main([
                "--links", str(links_file),
                "--output", str(out),
            ])
        assert code == 2

    def test_exit_2_when_engine_not_registered(self, links_file, tmp_path):
        out = tmp_path / "report.json"
        with patch(
            "scripts.sionna_rt_validation_gate.get_engine",
            side_effect=KeyError("sionna-rt"),
        ):
            code = main([
                "--links", str(links_file),
                "--output", str(out),
            ])
        assert code == 2

    def test_report_parent_dir_created(self, links_file, tmp_path):
        """Output inside a non-existent subdirectory should be created."""
        out = tmp_path / "sub" / "nested" / "report.json"
        rt_eng = _make_fake_engine("sionna-rt", available=True, loss_db=122.0)
        with (
            patch("scripts.sionna_rt_validation_gate.get_engine", return_value=rt_eng),
            patch(
                "scripts.sionna_rt_validation_gate.compare",
                side_effect=[
                    _make_comparison_result(122.0, 120.0),
                    _make_comparison_result(160.0, 140.0),
                ],
            ),
        ):
            code = main(["--links", str(links_file), "--output", str(out)])
        assert code == 0
        assert out.exists()

    def test_custom_thresholds_honoured(self, links_file, tmp_path):
        """With very loose thresholds any numeric result should pass."""
        code, _ = self._run(
            links_file, tmp_path,
            rt_loss=200.0, p1_loss=120.0,  # RMSE=80
            mmwave_rt=141.0, mmwave_p1=140.0,  # Δ=1
            sub6_max=999.0,
            mmwave_min=-999.0,
        )
        assert code == 0
