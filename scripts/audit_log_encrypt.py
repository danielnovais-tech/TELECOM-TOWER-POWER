#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Re-encrypt historical ``audit_log.metadata_json`` cleartext rows.

Reads rows whose ``metadata_json`` does NOT yet start with the
``"kms:v1:"`` envelope-encryption prefix, encrypts them via the same
KMS-wrapped DEK pool used by live writes, and updates them in place.

The job is idempotent: re-running on already-encrypted rows is a
no-op because the WHERE clause filters them out. It is also safe to
run while the API is live; rows are updated by primary key one batch
at a time, holding short transactions.

Usage::

    python scripts/audit_log_encrypt.py
    python scripts/audit_log_encrypt.py --batch-size 500 --max-rows 10000
    python scripts/audit_log_encrypt.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure the repo root is importable when invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audit_log  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Encrypt historical audit_log rows.")
    p.add_argument("--batch-size", type=int, default=200,
                   help="Rows per transaction (default 200).")
    p.add_argument("--max-rows", type=int, default=0,
                   help="Hard cap on total rows touched in this run "
                        "(0 = unlimited).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report counts only; do not write.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        print('{"error": "DATABASE_URL not set"}', file=sys.stderr)
        return 2
    if not audit_log._KMS_KEY_ID:  # noqa: SLF001
        print('{"error": "AUDIT_KMS_KEY_ID not set"}', file=sys.stderr)
        return 2

    import psycopg2  # type: ignore

    started = time.time()
    encrypted = 0
    skipped = 0
    errors = 0
    with psycopg2.connect(db_url) as conn:
        # Count first (cheap, surfaces backlog size before any write).
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE metadata_json IS NOT NULL "
                "  AND metadata_json NOT LIKE 'kms:v1:%'"
            )
            backlog = cur.fetchone()[0]
        if args.dry_run:
            import json as _json
            print(_json.dumps({
                "dry_run": True,
                "backlog_rows": backlog,
            }))
            return 0

        while True:
            if args.max_rows and encrypted >= args.max_rows:
                break
            limit = min(
                args.batch_size,
                args.max_rows - encrypted if args.max_rows else args.batch_size,
            )
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, metadata_json FROM audit_log "
                    " WHERE metadata_json IS NOT NULL "
                    "   AND metadata_json NOT LIKE 'kms:v1:%%' "
                    " ORDER BY id ASC "
                    " LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
            if not rows:
                break
            for row_id, plaintext in rows:
                token = audit_log._encrypt_metadata_blob(plaintext)  # noqa: SLF001
                if token is None:
                    # KMS unavailable; bail to avoid silently leaving
                    # rows in a half-migrated state across batches.
                    errors += 1
                    print(
                        f'{{"error": "encrypt_failed", "row_id": {row_id}}}',
                        file=sys.stderr,
                    )
                    return 3
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE audit_log SET metadata_json = %s WHERE id = %s",
                        (token, row_id),
                    )
                encrypted += 1
            conn.commit()
            if len(rows) < limit:
                break

    import json as _json
    print(_json.dumps({
        "dry_run": False,
        "encrypted": encrypted,
        "skipped": skipped,
        "errors": errors,
        "elapsed_seconds": round(time.time() - started, 3),
    }))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
