#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
refresh_brazil_towers.py – Weekly refresh script for Brazilian tower data.

Designed for cron / scheduled tasks.  Downloads the latest OpenCelliD data
and inserts only new towers (ON CONFLICT DO NOTHING) so existing records
are preserved.

Cron example (every Sunday at 02:00):
    0 2 * * 0 cd /app && python refresh_brazil_towers.py >> /var/log/tower_refresh.log 2>&1

Environment variables:
    OPENCELLID_TOKEN  – required (free from https://opencellid.org)
    DATABASE_URL      – PostgreSQL connection string (optional, uses SQLite otherwise)
"""

import argparse
import os
import sys
import time
from datetime import datetime

from tower_db import TowerStore


def refresh_opencellid(*, token: str, min_samples: int = 2,
                       use_copy: bool = False) -> int:
    """Download latest OpenCelliD data and insert new towers only."""
    from load_opencellid import download_brazil_csv, parse_opencellid_csv

    print(f"[{datetime.now().isoformat()}] Starting OpenCelliD refresh...")

    csv_path = download_brazil_csv(token)
    towers = parse_opencellid_csv(csv_path, min_samples=min_samples)

    if not towers:
        print("  No towers parsed.")
        return 0

    store = TowerStore()
    before = store.count()

    if use_copy and store.backend == "postgresql":
        print(f"  COPY-inserting {len(towers)} towers (DO NOTHING on conflict)...")
        loaded = store.copy_from_towers(towers, on_conflict="nothing")
    else:
        print(f"  Upserting {len(towers)} towers in batches...")
        loaded = 0
        batch_size = 5000
        for i in range(0, len(towers), batch_size):
            batch = towers[i : i + batch_size]
            loaded += store.upsert_many(batch)

    after = store.count()
    net_new = after - before
    print(f"  Done. {net_new} net new towers added (total: {after:,})")
    return net_new


def main():
    parser = argparse.ArgumentParser(
        description="Weekly refresh of Brazilian cell tower data"
    )
    parser.add_argument(
        "--token",
        default=os.getenv("OPENCELLID_TOKEN"),
        help="OpenCelliD API token (or set OPENCELLID_TOKEN env var)",
    )
    parser.add_argument(
        "--min-samples", type=int, default=2,
        help="Skip cells with fewer than N samples (default: 2)",
    )
    parser.add_argument(
        "--use-copy", action="store_true",
        help="Use PostgreSQL COPY for bulk import (PG only)",
    )
    args = parser.parse_args()

    if not args.token:
        print("ERROR: OPENCELLID_TOKEN env var or --token required",
              file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    net_new = refresh_opencellid(
        token=args.token,
        min_samples=args.min_samples,
        use_copy=args.use_copy,
    )
    elapsed = time.time() - t0
    print(f"[{datetime.now().isoformat()}] Refresh complete in {elapsed:.1f}s. "
          f"{net_new} new towers.")


if __name__ == "__main__":
    main()
