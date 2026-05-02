# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Coverage-diff robot — runs the engine A/B compare on a golden set.

Invoked nightly by .github/workflows/coverage-diff.yml. Failure modes:

* exit 1 if any engine's loss has drifted more than ``--threshold-db``
  versus the previous run on the same link;
* exit 0 (with the report still written) otherwise.

The golden link set is intentionally tiny (~20 links across urban /
suburban / Amazon biome) — the goal is to catch large regressions in
third-party engines, not to be a full regression suite.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Make sibling modules importable when run from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rf_engines.compare import compare  # noqa: E402

logger = logging.getLogger("coverage_diff_robot")


def _run_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, link in enumerate(links):
        # Strip JSON-only metadata before forwarding to engines.
        clean = {k: v for k, v in link.items() if not k.startswith("_")}
        result = compare(**clean)
        out.append({"link_index": i, "input": clean, "result": result.to_dict()})
    return out


def _detect_regressions(
    current: list[dict], previous: list[dict] | None, threshold: float
) -> list[str]:
    if previous is None:
        return []
    regressions: list[str] = []
    prev_by_idx = {p["link_index"]: p for p in previous}
    for cur in current:
        prev = prev_by_idx.get(cur["link_index"])
        if prev is None:
            continue
        prev_rows = {r["engine"]: r for r in prev["result"]["rows"]}
        for row in cur["result"]["rows"]:
            prev_row = prev_rows.get(row["engine"])
            if not prev_row or row["basic_loss_db"] is None or prev_row["basic_loss_db"] is None:
                continue
            drift = abs(row["basic_loss_db"] - prev_row["basic_loss_db"])
            if drift > threshold:
                regressions.append(
                    f"link={cur['link_index']} engine={row['engine']} "
                    f"prev={prev_row['basic_loss_db']:.1f}dB "
                    f"curr={row['basic_loss_db']:.1f}dB drift={drift:.1f}dB"
                )
    return regressions


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--links", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--previous-report", type=Path, default=None)
    p.add_argument("--threshold-db", type=float, default=3.0)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    links = json.loads(args.links.read_text())
    current = _run_links(links)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(current, indent=2, default=str))
    logger.info("wrote %s (%d links)", args.output, len(current))

    previous = None
    if args.previous_report and args.previous_report.is_file():
        try:
            previous = json.loads(args.previous_report.read_text())
        except Exception:
            logger.warning("could not read previous report; skipping drift check")

    regressions = _detect_regressions(current, previous, args.threshold_db)
    if regressions:
        for r in regressions:
            logger.error("REGRESSION: %s", r)
        return 1
    logger.info("no regressions above %.1f dB", args.threshold_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
