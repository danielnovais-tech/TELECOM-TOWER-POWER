"""
migrate_csv_to_db.py – Load towers from a CSV file directly into the database.

Supports both SQLite (default) and PostgreSQL (when DATABASE_URL is set).

Usage:
    python migrate_csv_to_db.py                            # default towers_brazil.csv
    python migrate_csv_to_db.py --csv towers_brazil.csv    # explicit CSV
    python migrate_csv_to_db.py --clear                    # wipe table first
"""

import argparse
import csv
import sys

from tower_db import TowerStore

DEFAULT_CSV = "towers_brazil.csv"


def migrate(csv_path: str, clear: bool = False):
    store = TowerStore()
    print(f"Database backend: {store.backend}")

    if clear:
        # Re-create the table by deleting all rows
        existing = store.list_all(limit=100_000)
        for row in existing:
            store.delete(row["id"])
        print(f"Cleared {len(existing)} existing towers")

    before = store.count()

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "id": row["id"].strip(),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "height_m": float(row["height_m"]),
                "operator": row["operator"].strip(),
                "bands": [b.strip() for b in row["bands"].split(",")],
                "power_dbm": float(row["power_dbm"]),
            })

    written = store.upsert_many(rows)
    after = store.count()

    print(f"Loaded {written} towers from {csv_path}")
    print(f"  Before: {before}  →  After: {after}")


def main():
    parser = argparse.ArgumentParser(
        description="Load towers from CSV into the database (SQLite or PostgreSQL)"
    )
    parser.add_argument(
        "--csv", default=DEFAULT_CSV,
        help=f"Path to towers CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Delete all existing towers before loading",
    )
    args = parser.parse_args()
    migrate(args.csv, clear=args.clear)


if __name__ == "__main__":
    main()
