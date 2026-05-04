# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Sionna RT validation gate — T10.

Runs the ``sionna-rt`` engine against a golden link set and enforces
two promotion criteria before allowing ``SIONNA_RT_DISABLED`` to be
flipped to ``0`` in production:

Criterion A — sub-6 GHz RMSE ≤ ``--sub6-rmse-db-max`` (default 6.0):
    ITU-R P.1812 is the accepted reference for sub-6 GHz. A > 6 dB
    RMSE means the ray tracer's geometry or material parameters are
    wrong — don't ship a model that's worse than the physics formula
    it's supposed to improve on.

Criterion B — mmWave mean Δ > ``--mmwave-delta-db-min`` (default 10.0):
    P.1812 is not valid above ≈ 6 GHz (it was designed for ≤ 3 GHz and
    the extrapolation degrades fast). At 28/39/60 GHz in dense urban
    canyons the true loss exceeds the P.1812 estimate by 15-30 dB; a
    mean delta < 10 dB would mean the Mitsuba scene contains almost no
    buildings (degenerate scene), the frequency was not set correctly,
    or reflections are being double-counted.

Usage
-----
    # Run against the bundled golden set (requires SIONNA_RT_DISABLED=0
    # and a valid SIONNA_RT_SCENE_PATH):
    python scripts/sionna_rt_validation_gate.py \\
        --links tests/data/sionna_rt_golden_links.json \\
        --output /tmp/rt_gate.json

    # Override thresholds (e.g. for a scene without buildings — FSPL
    # only — to validate plumbing, not physics):
    python scripts/sionna_rt_validation_gate.py \\
        --links tests/data/sionna_rt_golden_links.json \\
        --sub6-rmse-db-max 999 \\
        --mmwave-delta-db-min -999 \\
        --output /tmp/rt_gate.json

Exit codes
----------
0 — both criteria pass.
1 — one or more criteria fail; details in ``--output`` JSON.
2 — sionna-rt engine unavailable (SIONNA_RT_DISABLED=1 or deps missing).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

# Make sibling modules importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rf_engines import get_engine, list_engines  # noqa: E402
from rf_engines.compare import compare           # noqa: E402

logger = logging.getLogger("sionna_rt_validation_gate")

_SUB6_RMSE_MAX_DB = 6.0
_MMWAVE_DELTA_MIN_DB = 10.0


# ── Core maths ───────────────────────────────────────────────────

def rmse(errors: list[float]) -> float:
    """Root-mean-square error of a list of residuals."""
    if not errors:
        raise ValueError("empty residual list")
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def mean(values: list[float]) -> float:
    if not values:
        raise ValueError("empty list")
    return sum(values) / len(values)


# ── Link runner ──────────────────────────────────────────────────

def run_links(
    links: list[dict[str, Any]],
    rt_engine_name: str = "sionna-rt",
    p1812_engine_name: str = "itu-p1812",
) -> list[dict[str, Any]]:
    """Run every link through both engines; return per-link result rows."""
    rows = []
    for i, link in enumerate(links):
        band = link.get("_band", "unknown")
        clean = {k: v for k, v in link.items() if not k.startswith("_")}
        result = compare(
            engine_names=[rt_engine_name, p1812_engine_name],
            reference=p1812_engine_name,
            **clean,
        )
        by_engine = {r.engine: r for r in result.rows}
        rt_row = by_engine.get(rt_engine_name)
        p1_row = by_engine.get(p1812_engine_name)
        rows.append({
            "link_index": i,
            "band": band,
            "f_hz": link.get("f_hz"),
            "sionna_rt_loss_db": rt_row.basic_loss_db if rt_row else None,
            "itu_p1812_loss_db": p1_row.basic_loss_db if p1_row else None,
            "delta_db": rt_row.delta_db if rt_row else None,
            "sionna_rt_available": rt_row.available if rt_row else False,
        })
    return rows


# ── Criteria evaluation ──────────────────────────────────────────

def evaluate(
    link_rows: list[dict[str, Any]],
    *,
    sub6_rmse_max: float = _SUB6_RMSE_MAX_DB,
    mmwave_delta_min: float = _MMWAVE_DELTA_MIN_DB,
) -> dict[str, Any]:
    """Return a dict describing pass/fail for each criterion."""
    sub6_residuals: list[float] = []
    mmwave_deltas: list[float] = []

    skipped_sub6 = 0
    skipped_mmwave = 0

    for row in link_rows:
        rt = row.get("sionna_rt_loss_db")
        p1 = row.get("itu_p1812_loss_db")
        band = row.get("band", "")

        if band == "sub6":
            if rt is None or p1 is None:
                skipped_sub6 += 1
                continue
            sub6_residuals.append(rt - p1)
        elif band == "mmwave":
            delta = row.get("delta_db")
            if delta is None:
                skipped_mmwave += 1
                continue
            mmwave_deltas.append(delta)

    sub6_rmse_db: float | None = None
    criterion_a_pass: bool | None = None
    if sub6_residuals:
        sub6_rmse_db = rmse(sub6_residuals)
        criterion_a_pass = sub6_rmse_db <= sub6_rmse_max

    mmwave_mean_delta_db: float | None = None
    criterion_b_pass: bool | None = None
    if mmwave_deltas:
        mmwave_mean_delta_db = mean(mmwave_deltas)
        criterion_b_pass = mmwave_mean_delta_db > mmwave_delta_min

    overall_pass = bool(
        criterion_a_pass is not False and criterion_b_pass is not False
        and (criterion_a_pass is True or criterion_b_pass is True)
    )

    return {
        "criterion_a": {
            "name": "sub6_rmse_db",
            "threshold": f"<= {sub6_rmse_max} dB",
            "value": sub6_rmse_db,
            "links_evaluated": len(sub6_residuals),
            "links_skipped": skipped_sub6,
            "pass": criterion_a_pass,
        },
        "criterion_b": {
            "name": "mmwave_mean_delta_db",
            "threshold": f"> {mmwave_delta_min} dB",
            "value": mmwave_mean_delta_db,
            "links_evaluated": len(mmwave_deltas),
            "links_skipped": skipped_mmwave,
            "pass": criterion_b_pass,
        },
        "overall_pass": overall_pass,
    }


# ── CLI ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Sionna RT promotion validation gate.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--links", required=True, type=Path,
                   help="Path to golden links JSON file.")
    p.add_argument("--output", required=True, type=Path,
                   help="Path to write the JSON report.")
    p.add_argument("--sub6-rmse-db-max", type=float, default=_SUB6_RMSE_MAX_DB,
                   help="Maximum allowed RMSE (dB) on sub-6 GHz links vs. P.1812.")
    p.add_argument("--mmwave-delta-db-min", type=float, default=_MMWAVE_DELTA_MIN_DB,
                   help="Minimum required mean Δ (dB) on mmWave links vs. P.1812.")
    p.add_argument("--rt-engine", default="sionna-rt",
                   help="Name of the RT engine in the registry.")
    p.add_argument("--p1812-engine", default="itu-p1812",
                   help="Name of the P.1812 reference engine.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Guard: refuse to run if the engine isn't available.
    try:
        rt_eng = get_engine(args.rt_engine)
    except KeyError:
        logger.error("engine %r not registered", args.rt_engine)
        return 2
    if not rt_eng.is_available():
        logger.error(
            "engine %r is not available. "
            "Set SIONNA_RT_DISABLED=0, SIONNA_RT_SCENE_PATH, and ensure "
            "mitsuba + sionna_rt are installed.",
            args.rt_engine,
        )
        return 2

    links = json.loads(args.links.read_text())
    link_rows = run_links(links, args.rt_engine, args.p1812_engine)
    criteria = evaluate(
        link_rows,
        sub6_rmse_max=args.sub6_rmse_db_max,
        mmwave_delta_min=args.mmwave_delta_db_min,
    )

    report = {
        "gate": args.rt_engine,
        "links_file": str(args.links),
        "criteria": criteria,
        "link_rows": link_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))

    if criteria["overall_pass"]:
        logger.info("PASS — sionna-rt meets promotion criteria.")
        logger.info(
            "  Criterion A (sub-6 RMSE): %.2f dB  (threshold ≤ %.1f dB)",
            criteria["criterion_a"]["value"] or 0.0,
            args.sub6_rmse_db_max,
        )
        logger.info(
            "  Criterion B (mmWave Δ):   %.2f dB  (threshold > %.1f dB)",
            criteria["criterion_b"]["value"] or 0.0,
            args.mmwave_delta_db_min,
        )
        return 0

    logger.error("FAIL — sionna-rt does NOT meet promotion criteria.")
    if criteria["criterion_a"]["pass"] is False:
        logger.error(
            "  Criterion A FAIL: RMSE=%.2f dB > threshold %.1f dB",
            criteria["criterion_a"]["value"],
            args.sub6_rmse_db_max,
        )
    if criteria["criterion_b"]["pass"] is False:
        logger.error(
            "  Criterion B FAIL: mean Δ=%.2f dB ≤ threshold %.1f dB",
            criteria["criterion_b"]["value"],
            args.mmwave_delta_db_min,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
