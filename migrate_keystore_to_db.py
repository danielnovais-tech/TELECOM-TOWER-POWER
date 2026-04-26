#!/usr/bin/env python3
"""
migrate_keystore_to_db.py

One-shot backfill: read ``key_store.json`` and upsert every record into the
``api_keys`` PostgreSQL table.  Idempotent (uses the backend's UPSERT path),
safe to re-run.

Usage:
    DATABASE_URL=postgresql://... \\
        KEY_STORE_PATH=./key_store.json \\
        python migrate_keystore_to_db.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        log.error("DATABASE_URL not set; refusing to run (use the JSON file directly).")
        return 2

    src = Path(os.getenv("KEY_STORE_PATH", "./key_store.json"))
    if not src.exists():
        log.warning("Key store file %s not found; nothing to backfill.", src)
        return 0

    import key_store_db

    backend = key_store_db.get_backend()
    if backend.backend != "postgres":
        log.error(
            "Backend resolved to %s, expected 'postgres'. Check DATABASE_URL/psycopg2.",
            backend.backend,
        )
        return 3

    try:
        data = json.loads(src.read_text())
    except Exception as exc:  # noqa: BLE001
        log.error("Could not parse %s: %s", src, exc)
        return 4

    inserted = updated = skipped = 0
    for key, record in data.items():
        if not key.startswith("ttp_") and not key.startswith("demo_"):
            log.warning("Skipping suspicious key %r (no ttp_/demo_ prefix)", key[:8])
            skipped += 1
            continue
        if not isinstance(record, dict) or "tier" not in record or "email" not in record:
            log.warning("Skipping malformed record for %s\u2026", key[:12])
            skipped += 1
            continue

        existed = backend.lookup_key(key) is not None
        backend.upsert_key(key, record)
        if existed:
            updated += 1
        else:
            inserted += 1

    log.info(
        "Backfill complete: %d inserted, %d updated, %d skipped (out of %d).",
        inserted, updated, skipped, len(data),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
