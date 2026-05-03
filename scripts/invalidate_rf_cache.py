# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Invalidate hop_cache entries for towers flagged by the satellite-change robot.

Reads the JSON report produced by ``scripts/satellite_change_robot.py``
and POSTs the flagged tower IDs to ``POST /admin/cache/invalidate-towers``
on the production API. The endpoint writes per-tower stale markers in
Redis; the next ``plan_repeater`` call that touches one of those towers
forces a recompute and clears the marker.

Why this script lives here rather than in the API container
-----------------------------------------------------------
The satellite-change workflow runs on a GitHub Actions runner that has
no VPC access to ElastiCache; it can only reach the public API. Pushing
the invalidation through an authenticated HTTP endpoint keeps the
runner stateless and avoids exposing the cache to the public internet.

Usage
-----
    PLANET_REPORT=artifacts/satellite_change.json \
    TTP_API_URL=https://api.telecomtowerpower.com.br \
    TTP_ADMIN_API_KEY=... \
    python scripts/invalidate_rf_cache.py

Exit codes
----------
0 — success (or no flagged sites; nothing to do)
1 — report missing/malformed
2 — API call failed
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("invalidate_rf_cache")


def _flagged_ids(report: dict) -> list[str]:
    out: list[str] = []
    for site in report.get("sites", []) or []:
        if site.get("flagged"):
            name = site.get("name") or site.get("id")
            if name:
                out.append(str(name))
    return out


def _post(url: str, payload: dict, api_key: str, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": "ttp-invalidate-rf-cache/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--report", type=Path,
                   default=Path(os.environ.get("PLANET_REPORT", "artifacts/satellite_change.json")),
                   help="Path to satellite_change.json")
    p.add_argument("--api-url", default=os.environ.get("TTP_API_URL"),
                   help="Base URL of the API (e.g. https://api.telecomtowerpower.com.br)")
    p.add_argument("--api-key", default=os.environ.get("TTP_ADMIN_API_KEY"),
                   help="Admin API key (X-API-Key header)")
    p.add_argument("--reason", default="satellite-change")
    p.add_argument("--ttl-s", type=int, default=None,
                   help="Optional override for the stale-marker TTL")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the payload that would be POSTed and exit")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if not args.report.exists():
        logger.error("report file not found: %s", args.report)
        return 1
    try:
        report = json.loads(args.report.read_text())
    except json.JSONDecodeError as e:
        logger.error("malformed report: %s", e)
        return 1

    ids = _flagged_ids(report)
    if not ids:
        logger.info("no flagged sites in %s — nothing to invalidate", args.report)
        return 0

    payload = {"tower_ids": ids, "reason": args.reason}
    if args.ttl_s is not None:
        payload["ttl_s"] = args.ttl_s

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    if not args.api_url or not args.api_key:
        logger.error("TTP_API_URL and TTP_ADMIN_API_KEY are required (or pass --api-url / --api-key)")
        return 2

    url = args.api_url.rstrip("/") + "/admin/cache/invalidate-towers"
    try:
        result = _post(url, payload, args.api_key)
    except urllib.error.HTTPError as e:
        logger.error("API returned HTTP %s: %s", e.code, e.read().decode("utf-8", "replace")[:500])
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.error("API call failed: %s", e)
        return 2

    logger.info("invalidated %s/%s towers (reason=%s)",
                result.get("marked_stale"), result.get("requested"), args.reason)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
