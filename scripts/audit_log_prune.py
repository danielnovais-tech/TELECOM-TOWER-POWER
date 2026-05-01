#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Daily retention enforcement for the ``audit_log`` table.

Run from a cron / GitHub Actions job. Reads ``DATABASE_URL`` and the
two retention windows from the environment; falls back to module
defaults (365 days security, 90 days operational) when unset.

Exits non-zero on database errors so the scheduler surfaces them —
silent retention drift is the failure mode auditors care about.

Usage::

    python scripts/audit_log_prune.py
    python scripts/audit_log_prune.py --security-days 365 --operational-days 90
    python scripts/audit_log_prune.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the repo root is importable when invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audit_log  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prune old audit_log rows.")
    p.add_argument("--security-days", type=int, default=None,
                   help="Retention for security-sensitive actions "
                        "(env: AUDIT_RETENTION_SECURITY_DAYS, default 365).")
    p.add_argument("--operational-days", type=int, default=None,
                   help="Retention for operational actions "
                        "(env: AUDIT_RETENTION_OPERATIONAL_DAYS, default 90).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report counts without deleting (PostgreSQL only).")
    return p.parse_args()


def _dry_run(security_days: int, operational_days: int) -> dict:
    import time
    import psycopg2  # type: ignore

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL not set; --dry-run requires PostgreSQL.")
    now = time.time()
    sec_cutoff = now - security_days * 86400.0
    op_cutoff = now - operational_days * 86400.0
    prefixes = audit_log._SECURITY_ACTION_PREFIXES  # noqa: SLF001
    sec_clauses = " OR ".join(["lower(action) LIKE %s"] * len(prefixes))
    sec_params = [p + "%" for p in prefixes]
    out = {"security_would_delete": 0, "operational_would_delete": 0}
    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM audit_log WHERE ts < %s AND ({sec_clauses})",
            [sec_cutoff, *sec_params],
        )
        out["security_would_delete"] = cur.fetchone()[0]
        cur.execute(
            f"SELECT COUNT(*) FROM audit_log WHERE ts < %s AND NOT ({sec_clauses})",
            [op_cutoff, *sec_params],
        )
        out["operational_would_delete"] = cur.fetchone()[0]
    return out


def main() -> int:
    args = _parse_args()
    sec = args.security_days if args.security_days is not None else int(
        os.getenv("AUDIT_RETENTION_SECURITY_DAYS", "365"))
    ope = args.operational_days if args.operational_days is not None else int(
        os.getenv("AUDIT_RETENTION_OPERATIONAL_DAYS", "90"))
    if args.dry_run:
        result = _dry_run(sec, ope)
    else:
        result = audit_log.prune(security_days=sec, operational_days=ope)
    print(json.dumps({
        "ok": True,
        "security_days": sec,
        "operational_days": ope,
        "dry_run": args.dry_run,
        **result,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
