"""
load_brazil_towers.py – Unified pipeline to load ALL Brazilian cell towers
from both OpenCelliD and ANATEL data sources.

This orchestration script runs both loaders in sequence:
  1. OpenCelliD (primary) – geo-referenced crowd-sourced cell data
  2. ANATEL (supplementary) – official licensed station data (geocoded)

Usage:
    # Load from both sources
    python load_brazil_towers.py \\
        --opencellid-token pk_xxxxxxx \\
        --anatel-file ERBs_com_equipamentos_v2.xlsx

    # OpenCelliD only (from a local download)
    python load_brazil_towers.py --opencellid-file 724.csv.gz

    # ANATEL only
    python load_brazil_towers.py --anatel-file anatel_data.xlsx

    # Dry run – preview without DB writes
    python load_brazil_towers.py --opencellid-file 724.csv.gz --dry-run

    # Use PG COPY for fast bulk import
    python load_brazil_towers.py --opencellid-file 724.csv.gz --use-copy

    # Stats only – show current DB state
    python load_brazil_towers.py --stats

    # Validate data quality after loading
    python load_brazil_towers.py --validate
"""

import argparse
import os
import sys
import time

from tower_db import TowerStore


def _print_separator(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def _print_stats(store: TowerStore) -> None:
    total = store.count()
    print(f"\nDatabase: {store.backend}")
    print(f"Total towers: {total:,}")

    # Sample by operator
    operators: dict[str, int] = {}
    all_towers = store.list_all(limit=total)
    for t in all_towers:
        op = t["operator"]
        operators[op] = operators.get(op, 0) + 1

    if operators:
        print("\nTowers by operator:")
        for op, count in sorted(operators.items(), key=lambda x: -x[1]):
            pct = count / total * 100 if total else 0
            print(f"  {op:15s} {count:>8,}  ({pct:5.1f}%)")

    # Source breakdown
    ocid = sum(1 for t in all_towers if t["id"].startswith("OCID_"))
    anatel = sum(1 for t in all_towers if t["id"].startswith("ANATEL_"))
    other = total - ocid - anatel
    print(f"\nTowers by source:")
    print(f"  OpenCelliD     {ocid:>8,}")
    print(f"  ANATEL         {anatel:>8,}")
    if other:
        print(f"  Other          {other:>8,}")


def _validate_data(store: TowerStore) -> None:
    """Run data quality checks on the loaded tower data."""
    _print_separator("Data Validation")
    total = store.count()
    if total == 0:
        print("No towers in database – nothing to validate.")
        return

    all_towers = store.list_all(limit=total)

    # 1. Count by operator
    operators: dict[str, int] = {}
    for t in all_towers:
        operators[t["operator"]] = operators.get(t["operator"], 0) + 1
    print("1. Towers per operator:")
    for op, cnt in sorted(operators.items(), key=lambda x: -x[1]):
        print(f"   {op:15s} {cnt:>8,}")

    # 2. Outliers outside Brazil bounding box
    outliers = [
        t for t in all_towers
        if not (-34.0 <= t["lat"] <= 6.0 and -74.0 <= t["lon"] <= -28.0)
    ]
    print(f"\n2. Towers outside Brazil bounding box: {len(outliers)}")
    if outliers:
        for t in outliers[:5]:
            print(f"   {t['id']:40s}  ({t['lat']:.4f}, {t['lon']:.4f})")
        if len(outliers) > 5:
            print(f"   ... and {len(outliers) - 5} more")

    # 3. Duplicate coordinates (same lat/lon rounded to 4 decimals, same operator)
    coord_key = {}
    dupes = 0
    for t in all_towers:
        key = (round(t["lat"], 4), round(t["lon"], 4), t["operator"])
        if key in coord_key:
            dupes += 1
        else:
            coord_key[key] = t["id"]
    print(f"\n3. Duplicate coordinate+operator pairs: {dupes}")

    # 4. Source overlap (OCID and ANATEL towers within 50m of each other)
    ocid_towers = [t for t in all_towers if t["id"].startswith("OCID_")]
    anatel_towers = [t for t in all_towers if t["id"].startswith("ANATEL_")]
    print(f"\n4. Source counts:")
    print(f"   OpenCelliD:   {len(ocid_towers):>8,}")
    print(f"   ANATEL:       {len(anatel_towers):>8,}")
    print(f"   Other:        {total - len(ocid_towers) - len(anatel_towers):>8,}")

    # 5. Missing/empty operators
    empty_op = sum(1 for t in all_towers if not t["operator"].strip())
    print(f"\n5. Towers with empty operator: {empty_op}")

    # 6. Height distribution
    heights = [t["height_m"] for t in all_towers]
    if heights:
        print(f"\n6. Tower height stats:")
        print(f"   Min:  {min(heights):.1f}m")
        print(f"   Max:  {max(heights):.1f}m")
        print(f"   Avg:  {sum(heights)/len(heights):.1f}m")

    print(f"\n{'=' * 60}")
    issues = len(outliers) + empty_op
    if issues == 0:
        print("  All checks passed!")
    else:
        print(f"  {issues} potential issue(s) found")
    print(f"{'=' * 60}")


def _dedup_sources(store: TowerStore, prefer: str = "anatel",
                   threshold_m: float = 50.0) -> None:
    """Deduplicate towers where both OCID and ANATEL records exist nearby.

    Args:
        store: TowerStore instance.
        prefer: Which source to keep – ``"anatel"`` (official) or
                ``"ocid"`` (precise GPS).
        threshold_m: Maximum distance in metres to consider towers as
                     duplicates (default 50m).
    """
    _print_separator(
        f"Deduplication (within {threshold_m:.0f}m, prefer={prefer})"
    )
    total = store.count()
    if total == 0:
        print("No towers to deduplicate.")
        return

    # Use the DB-level query (earth_distance on PG, haversine on SQLite)
    print("  Searching for cross-source duplicates ...")
    dupes = store.find_duplicates(distance_m=threshold_m)

    # Keep only cross-source pairs (one OCID, one ANATEL)
    cross_source = []
    for d in dupes:
        a_is_ocid = d["id_a"].startswith("OCID_")
        a_is_anatel = d["id_a"].startswith("ANATEL_")
        b_is_ocid = d["id_b"].startswith("OCID_")
        b_is_anatel = d["id_b"].startswith("ANATEL_")
        if (a_is_ocid and b_is_anatel) or (a_is_anatel and b_is_ocid):
            # Normalise: ocid_id first, anatel_id second
            if a_is_anatel:
                d["id_a"], d["id_b"] = d["id_b"], d["id_a"]
            cross_source.append(d)

    same_source = len(dupes) - len(cross_source)
    print(f"  Total duplicate pairs within {threshold_m:.0f}m: {len(dupes)}")
    print(f"    Cross-source (OCID↔ANATEL):  {len(cross_source)}")
    print(f"    Same-source:                  {same_source}")

    if not cross_source:
        print("  No cross-source duplicates to resolve.")
        return

    removed_ids: list[str] = []
    kept_ids: list[str] = []
    for pair in cross_source:
        ocid_id = pair["id_a"]
        anatel_id = pair["id_b"]
        if prefer == "anatel":
            removed_ids.append(ocid_id)
            kept_ids.append(anatel_id)
        else:
            removed_ids.append(anatel_id)
            kept_ids.append(ocid_id)

    # Remove duplicates
    for tid in removed_ids:
        store.delete(tid)

    label = "ANATEL (official)" if prefer == "anatel" else "OpenCelliD (GPS)"
    print(f"  Kept {len(kept_ids)} {label} records")
    print(f"  Removed {len(removed_ids)} duplicate records")
    print(f"  Towers remaining: {store.count():,}")


def main():
    parser = argparse.ArgumentParser(
        description="Load Brazilian cell towers from OpenCelliD and ANATEL"
    )

    # OpenCelliD options
    ocid = parser.add_argument_group("OpenCelliD options")
    ocid.add_argument(
        "--opencellid-token",
        default=os.getenv("OPENCELLID_TOKEN"),
        help="OpenCelliD API token (or set OPENCELLID_TOKEN env var)",
    )
    ocid.add_argument(
        "--opencellid-file",
        help="Path to a local 724.csv.gz file (skips download)",
    )
    ocid.add_argument(
        "--min-samples", type=int, default=2,
        help="Skip OpenCelliD cells with fewer than N samples (default: 2)",
    )

    # ANATEL options
    anatel = parser.add_argument_group("ANATEL options")
    anatel.add_argument(
        "--anatel-file",
        help="Path to ANATEL XLSX or CSV file",
    )

    # General options
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max towers per source (0 = all)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000,
        help="DB insert batch size (default: 5000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and preview without writing to DB",
    )
    parser.add_argument(
        "--use-copy", action="store_true",
        help="Use PostgreSQL COPY for faster bulk import (PG only)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show current DB statistics and exit",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run data quality checks after loading (or standalone)",
    )
    parser.add_argument(
        "--dedup", action="store_true",
        help="Deduplicate cross-source towers within --dedup-threshold metres",
    )
    parser.add_argument(
        "--dedup-threshold", type=float, default=50.0,
        help="Distance threshold in metres for deduplication (default: 50)",
    )
    parser.add_argument(
        "--prefer", choices=["anatel", "ocid"], default="anatel",
        help="Which source to keep when duplicates are found "
             "(anatel=official, ocid=precise GPS; default: anatel)",
    )

    args = parser.parse_args()

    store = TowerStore()

    if args.stats:
        _print_stats(store)
        return

    has_ocid = args.opencellid_token or args.opencellid_file
    has_anatel = args.anatel_file

    if args.validate and not has_ocid and not has_anatel:
        _validate_data(store)
        return

    if args.dedup and not has_ocid and not has_anatel:
        _dedup_sources(store, prefer=args.prefer,
                       threshold_m=args.dedup_threshold)
        return

    if not has_ocid and not has_anatel:
        parser.error(
            "Provide at least one data source: "
            "--opencellid-token/--opencellid-file and/or --anatel-file"
        )

    before = store.count()
    t0 = time.time()
    ocid_count = 0
    anatel_count = 0

    # ── Phase 1: OpenCelliD ──────────────────────────────────────
    if has_ocid:
        _print_separator("Phase 1: OpenCelliD (Primary Source)")
        from load_opencellid import load_opencellid

        ocid_count = load_opencellid(
            token=args.opencellid_token,
            file_path=args.opencellid_file,
            limit=args.limit,
            min_samples=args.min_samples,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            use_copy=args.use_copy,
        )

    # ── Phase 2: ANATEL ──────────────────────────────────────────
    if has_anatel:
        _print_separator("Phase 2: ANATEL (Supplementary Source)")
        from load_anatel import load_anatel

        anatel_count = load_anatel(
            file_path=args.anatel_file,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )

    # ── Summary ──────────────────────────────────────────────────
    elapsed = time.time() - t0
    after = store.count()

    _print_separator("Summary")
    print(f"Elapsed time:     {elapsed:.1f}s")
    print(f"OpenCelliD:       {ocid_count:,} towers")
    print(f"ANATEL:           {anatel_count:,} towers")
    print(f"DB before:        {before:,}")
    print(f"DB after:         {after:,}")
    print(f"Net new:          {after - before:,}")

    if not args.dry_run:
        _print_stats(store)

    if args.dedup and not args.dry_run:
        _dedup_sources(store, prefer=args.prefer,
                       threshold_m=args.dedup_threshold)

    if args.validate and not args.dry_run:
        _validate_data(store)


if __name__ == "__main__":
    main()
