"""
load_brazil_towers.py – Unified pipeline to load ALL Brazilian cell towers
from both OpenCelliD and ANATEL data sources.

This orchestration script runs both loaders in sequence:
  1. OpenCelliD (primary) – geo-referenced crowd-sourced cell data
  2. ANATEL (supplementary) – official licensed station data (geocoded)
  3. Enrichment (optional) – cross-reference ANATEL towers with nearby
     OpenCelliD data to infer radio tech, bands, and power.

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

    # Enrich ANATEL towers with OpenCelliD radio tech data
    python load_brazil_towers.py --enrich
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

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


# ── Default bands/power for ANATEL towers with no OCID match ─────
_DEFAULT_ANATEL_BANDS = ["700MHz", "1800MHz", "2600MHz"]  # assume LTE
_DEFAULT_ANATEL_POWER = 46.0  # LTE typical


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two points."""
    R = 6_371_000
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _enrich_anatel_from_ocid(store: TowerStore, threshold_m: float = 2000.0,
                              dry_run: bool = False) -> None:
    """Enrich ANATEL towers with radio tech from nearby OpenCelliD towers.

    For each ANATEL tower that still has default bands (['700MHz','1800MHz']):
      1. Find the nearest OpenCelliD tower with the same operator within
         *threshold_m* metres.
      2. Copy its bands and power_dbm to the ANATEL tower.
      3. If no match, upgrade defaults to LTE (700/1800/2600 MHz, 46 dBm).

    Uses a spatial grid index for fast neighbour lookup (O(n) build, ~O(1) query).
    """
    _print_separator(
        f"Enrichment (ANATEL ← OpenCelliD, threshold={threshold_m:.0f}m)"
    )

    total = store.count()
    if total == 0:
        print("  No towers in database.")
        return

    all_towers = store.list_all(limit=total)
    ocid_towers = [t for t in all_towers if t["id"].startswith("OCID_")]
    anatel_towers = [t for t in all_towers if t["id"].startswith("ANATEL_")]

    if not ocid_towers:
        print("  No OpenCelliD towers to cross-reference.")
        return

    # Identify ANATEL towers still on original defaults (need enrichment)
    old_defaults = ['700MHz', '1800MHz']
    anatel_needing = [
        t for t in anatel_towers
        if t["bands"] == old_defaults and t["power_dbm"] == 43.0
    ]
    print(f"  ANATEL towers needing enrichment: {len(anatel_needing):,}")
    print(f"  OpenCelliD towers available:      {len(ocid_towers):,}")

    if not anatel_needing:
        print("  All ANATEL towers already enriched.")
        return

    # Build spatial grid index for OpenCelliD towers (grouped by operator)
    # Grid cell size ~0.02° ≈ 2.2 km at equator
    GRID_SIZE = 0.02
    grid: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for t in ocid_towers:
        gx = int(t["lat"] / GRID_SIZE)
        gy = int(t["lon"] / GRID_SIZE)
        grid[(gx, gy, t["operator"])].append(t)

    matched = 0
    upgraded_default = 0
    updates: list[dict] = []

    for at in anatel_needing:
        gx = int(at["lat"] / GRID_SIZE)
        gy = int(at["lon"] / GRID_SIZE)
        op = at["operator"]

        # Search in the 3×3 neighbourhood of grid cells
        best_dist = threshold_m + 1
        best_ocid = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ot in grid.get((gx + dx, gy + dy, op), []):
                    d = _haversine_m(at["lat"], at["lon"], ot["lat"], ot["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_ocid = ot

        if best_ocid is not None:
            at["bands"] = best_ocid["bands"]
            at["power_dbm"] = best_ocid["power_dbm"]
            matched += 1
        else:
            # No match — upgrade to LTE defaults (better than generic 2G)
            at["bands"] = _DEFAULT_ANATEL_BANDS
            at["power_dbm"] = _DEFAULT_ANATEL_POWER
            upgraded_default += 1

        updates.append(at)

    print(f"  Matched from OpenCelliD:  {matched:,}")
    print(f"  Upgraded to LTE default:  {upgraded_default:,}")

    if dry_run:
        print(f"\n  [DRY RUN] Would update {len(updates):,} towers. Samples:")
        for t in updates[:5]:
            bands_str = ",".join(t["bands"]) if isinstance(t["bands"], list) else t["bands"]
            print(f"    {t['id']:30s}  {t['operator']:10s}  "
                  f"{bands_str:30s}  {t['power_dbm']}dBm")
        return

    # Write updates back to DB in batches
    batch_size = 5000
    written = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i + batch_size]
        store.upsert_many(batch)
        written += len(batch)
    print(f"  Updated {written:,} ANATEL towers in database")

    # Print band distribution of enriched towers
    from collections import Counter
    band_counts: Counter[str] = Counter()
    for t in updates:
        bands = t["bands"]
        if isinstance(bands, str):
            bands = json.loads(bands)
        for b in bands:
            band_counts[b] += 1
    print(f"\n  Band distribution after enrichment:")
    for band, cnt in band_counts.most_common():
        print(f"    {band:12s}  {cnt:>8,}")


# ── Regeocode: improve ANATEL tower coordinates ──────────────────

def _regeocode_anatel(store: TowerStore, threshold_m: float = 5000.0,
                      dry_run: bool = False) -> None:
    """Improve ANATEL tower coordinates by snapping to nearby OpenCelliD towers.

    Strategy (applied per ANATEL tower, in priority order):
      1. **Same-operator snap**: Find the nearest OpenCelliD tower with the
         same operator within *threshold_m*. Place the ANATEL tower near it
         (50-300m offset to avoid exact overlap).
      2. **Any-operator snap**: If no same-operator OCID tower exists, use the
         nearest OCID tower of any operator (with larger offset, 200-500m).
      3. **Improved jitter**: If no OCID towers at all in the area, keep the
         municipality centroid but distribute towers using a deterministic
         spiral pattern instead of random jitter (more realistic spread).
    """
    _print_separator(
        f"Regeocode (ANATEL → OpenCelliD snap, threshold={threshold_m:.0f}m)"
    )

    total = store.count()
    all_towers = store.list_all(limit=total)
    ocid_towers = [t for t in all_towers if t["id"].startswith("OCID_")]
    anatel_towers = [t for t in all_towers if t["id"].startswith("ANATEL_")]

    if not anatel_towers:
        print("  No ANATEL towers to regeocode.")
        return
    if not ocid_towers:
        print("  No OpenCelliD towers for snapping. Applying spiral jitter only.")

    print(f"  ANATEL towers:   {len(anatel_towers):,}")
    print(f"  OpenCelliD refs: {len(ocid_towers):,}")

    # Build spatial grid index for OCID towers (all operators)
    GRID_SIZE = 0.05  # ~5.5 km cells
    grid_all: dict[tuple[int, int], list[dict]] = defaultdict(list)
    grid_op: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for t in ocid_towers:
        gx = int(t["lat"] / GRID_SIZE)
        gy = int(t["lon"] / GRID_SIZE)
        grid_all[(gx, gy)].append(t)
        grid_op[(gx, gy, t["operator"])].append(t)

    snapped_same_op = 0
    snapped_any_op = 0
    spiral_only = 0
    updates: list[dict] = []

    # Group ANATEL towers by municipality centroid (rounded to 3 decimals)
    # for deterministic spiral placement of unmatched towers
    centroid_groups: dict[tuple[float, float], list[dict]] = defaultdict(list)

    for at in anatel_towers:
        gx = int(at["lat"] / GRID_SIZE)
        gy = int(at["lon"] / GRID_SIZE)
        op = at["operator"]

        # Strategy 1: Same-operator snap (3x3 grid neighbourhood)
        best_dist = threshold_m + 1
        best_ocid = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ot in grid_op.get((gx + dx, gy + dy, op), []):
                    d = _haversine_m(at["lat"], at["lon"], ot["lat"], ot["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_ocid = ot

        if best_ocid is not None:
            # Place near the OCID tower with 50-300m offset
            offset_m = 50 + (hash(at["id"]) % 250)
            bearing = (hash(at["id"] + "b") % 360)
            new_lat, new_lon = _offset_point(
                best_ocid["lat"], best_ocid["lon"], offset_m, bearing
            )
            at["lat"] = round(new_lat, 6)
            at["lon"] = round(new_lon, 6)
            snapped_same_op += 1
            updates.append(at)
            continue

        # Strategy 2: Any-operator snap (3x3 grid neighbourhood)
        best_dist = threshold_m + 1
        best_ocid = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ot in grid_all.get((gx + dx, gy + dy), []):
                    d = _haversine_m(at["lat"], at["lon"], ot["lat"], ot["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_ocid = ot

        if best_ocid is not None:
            # Larger offset (200-500m) since different operator
            offset_m = 200 + (hash(at["id"]) % 300)
            bearing = (hash(at["id"] + "b") % 360)
            new_lat, new_lon = _offset_point(
                best_ocid["lat"], best_ocid["lon"], offset_m, bearing
            )
            at["lat"] = round(new_lat, 6)
            at["lon"] = round(new_lon, 6)
            snapped_any_op += 1
            updates.append(at)
            continue

        # Strategy 3: No OCID nearby — use spiral placement
        centroid_key = (round(at["lat"], 3), round(at["lon"], 3))
        centroid_groups[centroid_key].append(at)

    # Apply spiral placement to unmatched groups
    for (clat, clon), towers in centroid_groups.items():
        for i, at in enumerate(towers):
            # Fermat spiral: r grows with sqrt(i), angle by golden ratio
            r_deg = 0.003 + 0.001 * math.sqrt(i + 1)  # ~300m base + growth
            angle = i * 2.399963  # golden angle in radians
            at["lat"] = round(clat + r_deg * math.cos(angle), 6)
            at["lon"] = round(clon + r_deg * math.sin(angle), 6)
            spiral_only += 1
            updates.append(at)

    print(f"\n  Snapped to same-operator OCID:  {snapped_same_op:,}")
    print(f"  Snapped to any-operator OCID:   {snapped_any_op:,}")
    print(f"  Spiral placement (no OCID):     {spiral_only:,}")
    print(f"  Total to update:                {len(updates):,}")

    if dry_run:
        print(f"\n  [DRY RUN] Would update {len(updates):,} towers. Samples:")
        for t in updates[:8]:
            print(f"    {t['id']:30s}  {t['operator']:10s}  "
                  f"({t['lat']:.6f}, {t['lon']:.6f})")
        return

    # Write in batches
    batch_size = 5000
    written = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i + batch_size]
        store.upsert_many(batch)
        written += len(batch)
    print(f"  Updated {written:,} ANATEL tower coordinates")

    # Post-update stats
    updated_all = store.list_all(limit=store.count())
    updated_anatel = [t for t in updated_all if t["id"].startswith("ANATEL_")]
    from collections import Counter
    coords = Counter(
        (round(t["lat"], 4), round(t["lon"], 4)) for t in updated_anatel
    )
    print(f"\n  Coordinate uniqueness: {len(coords):,} unique / {len(updated_anatel):,} total")
    top = coords.most_common(3)
    print(f"  Max towers at same point: {top[0][1]}" if top else "")


def _offset_point(lat: float, lon: float, dist_m: float,
                  bearing_deg: float) -> tuple[float, float]:
    """Move a point by *dist_m* metres at *bearing_deg* degrees."""
    R = 6_371_000
    d = dist_m / R
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d)
        + math.cos(lat1) * math.sin(d) * math.cos(br)
    )
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


# ── Regeocode: improve ANATEL tower coordinates ──────────────────

def _regeocode_anatel(store: TowerStore, threshold_m: float = 5000.0,
                      dry_run: bool = False) -> None:
    """Improve ANATEL tower coordinates by snapping to nearby OpenCelliD towers.

    Strategy (applied per ANATEL tower, in priority order):
      1. **Same-operator snap**: Find the nearest OpenCelliD tower with the
         same operator within *threshold_m*. Place the ANATEL tower near it
         (50-300m offset to avoid exact overlap).
      2. **Any-operator snap**: If no same-operator OCID tower exists, use the
         nearest OCID tower of any operator (with larger offset, 200-500m).
      3. **Improved jitter**: If no OCID towers at all in the area, keep the
         municipality centroid but distribute towers using a deterministic
         spiral pattern instead of random jitter (more realistic spread).
    """
    _print_separator(
        f"Regeocode (ANATEL → OpenCelliD snap, threshold={threshold_m:.0f}m)"
    )

    total = store.count()
    all_towers = store.list_all(limit=total)
    ocid_towers = [t for t in all_towers if t["id"].startswith("OCID_")]
    anatel_towers = [t for t in all_towers if t["id"].startswith("ANATEL_")]

    if not anatel_towers:
        print("  No ANATEL towers to regeocode.")
        return
    if not ocid_towers:
        print("  No OpenCelliD towers for snapping. Applying spiral jitter only.")

    print(f"  ANATEL towers:   {len(anatel_towers):,}")
    print(f"  OpenCelliD refs: {len(ocid_towers):,}")

    # Build spatial grid index for OCID towers (all operators)
    GRID_SIZE = 0.05  # ~5.5 km cells
    grid_all: dict[tuple[int, int], list[dict]] = defaultdict(list)
    grid_op: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for t in ocid_towers:
        gx = int(t["lat"] / GRID_SIZE)
        gy = int(t["lon"] / GRID_SIZE)
        grid_all[(gx, gy)].append(t)
        grid_op[(gx, gy, t["operator"])].append(t)

    snapped_same_op = 0
    snapped_any_op = 0
    spiral_only = 0
    updates: list[dict] = []

    # Group ANATEL towers by municipality centroid (rounded to 3 decimals)
    # for deterministic spiral placement of unmatched towers
    centroid_groups: dict[tuple[float, float], list[dict]] = defaultdict(list)

    for at in anatel_towers:
        gx = int(at["lat"] / GRID_SIZE)
        gy = int(at["lon"] / GRID_SIZE)
        op = at["operator"]

        # Strategy 1: Same-operator snap (3x3 grid neighbourhood)
        best_dist = threshold_m + 1
        best_ocid = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ot in grid_op.get((gx + dx, gy + dy, op), []):
                    d = _haversine_m(at["lat"], at["lon"], ot["lat"], ot["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_ocid = ot

        if best_ocid is not None:
            # Place near the OCID tower with 50-300m offset
            offset_m = 50 + (hash(at["id"]) % 250)
            bearing = (hash(at["id"] + "b") % 360)
            new_lat, new_lon = _offset_point(
                best_ocid["lat"], best_ocid["lon"], offset_m, bearing
            )
            at["lat"] = round(new_lat, 6)
            at["lon"] = round(new_lon, 6)
            snapped_same_op += 1
            updates.append(at)
            continue

        # Strategy 2: Any-operator snap (3x3 grid neighbourhood)
        best_dist = threshold_m + 1
        best_ocid = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ot in grid_all.get((gx + dx, gy + dy), []):
                    d = _haversine_m(at["lat"], at["lon"], ot["lat"], ot["lon"])
                    if d < best_dist:
                        best_dist = d
                        best_ocid = ot

        if best_ocid is not None:
            # Larger offset (200-500m) since different operator
            offset_m = 200 + (hash(at["id"]) % 300)
            bearing = (hash(at["id"] + "b") % 360)
            new_lat, new_lon = _offset_point(
                best_ocid["lat"], best_ocid["lon"], offset_m, bearing
            )
            at["lat"] = round(new_lat, 6)
            at["lon"] = round(new_lon, 6)
            snapped_any_op += 1
            updates.append(at)
            continue

        # Strategy 3: No OCID nearby — use spiral placement
        centroid_key = (round(at["lat"], 3), round(at["lon"], 3))
        centroid_groups[centroid_key].append(at)

    # Apply spiral placement to unmatched groups
    for (clat, clon), towers in centroid_groups.items():
        for i, at in enumerate(towers):
            # Fermat spiral: r grows with sqrt(i), angle by golden ratio
            r_deg = 0.003 + 0.001 * math.sqrt(i + 1)  # ~300m base + growth
            angle = i * 2.399963  # golden angle in radians
            at["lat"] = round(clat + r_deg * math.cos(angle), 6)
            at["lon"] = round(clon + r_deg * math.sin(angle), 6)
            spiral_only += 1
            updates.append(at)

    print(f"\n  Snapped to same-operator OCID:  {snapped_same_op:,}")
    print(f"  Snapped to any-operator OCID:   {snapped_any_op:,}")
    print(f"  Spiral placement (no OCID):     {spiral_only:,}")
    print(f"  Total to update:                {len(updates):,}")

    if dry_run:
        print(f"\n  [DRY RUN] Would update {len(updates):,} towers. Samples:")
        for t in updates[:8]:
            print(f"    {t['id']:30s}  {t['operator']:10s}  "
                  f"({t['lat']:.6f}, {t['lon']:.6f})")
        return

    # Write in batches
    batch_size = 5000
    written = 0
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i + batch_size]
        store.upsert_many(batch)
        written += len(batch)
    print(f"  Updated {written:,} ANATEL tower coordinates")

    # Post-update stats
    updated_all = store.list_all(limit=store.count())
    updated_anatel = [t for t in updated_all if t["id"].startswith("ANATEL_")]
    from collections import Counter
    coords = Counter(
        (round(t["lat"], 4), round(t["lon"], 4)) for t in updated_anatel
    )
    print(f"\n  Coordinate uniqueness: {len(coords):,} unique / {len(updated_anatel):,} total")
    top = coords.most_common(3)
    print(f"  Max towers at same point: {top[0][1]}" if top else "")


def _offset_point(lat: float, lon: float, dist_m: float,
                  bearing_deg: float) -> tuple[float, float]:
    """Move a point by *dist_m* metres at *bearing_deg* degrees."""
    R = 6_371_000
    d = dist_m / R
    br = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d)
        + math.cos(lat1) * math.sin(d) * math.cos(br)
    )
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


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
        "--min-samples", type=int, default=1,
        help="Skip OpenCelliD cells with fewer than N samples (default: 1)",
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
        "--enrich", action="store_true",
        help="Enrich ANATEL towers with radio tech from nearby OpenCelliD data",
    )
    parser.add_argument(
        "--enrich-threshold", type=float, default=2000.0,
        help="Max distance in metres to match OCID→ANATEL towers (default: 2000)",
    )
    parser.add_argument(
        "--regeocode", action="store_true",
        help="Improve ANATEL tower coordinates by snapping to nearby OpenCelliD",
    )
    parser.add_argument(
        "--regeocode-threshold", type=float, default=5000.0,
        help="Max snap distance in metres for regeocode (default: 5000)",
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

    if args.enrich and not has_ocid and not has_anatel:
        _enrich_anatel_from_ocid(store, threshold_m=args.enrich_threshold,
                                 dry_run=args.dry_run)
        return

    if args.regeocode and not has_ocid and not has_anatel:
        _regeocode_anatel(store, threshold_m=args.regeocode_threshold,
                          dry_run=args.dry_run)
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

    if args.enrich and not args.dry_run:
        _enrich_anatel_from_ocid(store, threshold_m=args.enrich_threshold,
                                 dry_run=args.dry_run)

    if args.regeocode and not args.dry_run:
        _regeocode_anatel(store, threshold_m=args.regeocode_threshold,
                          dry_run=args.dry_run)

    if args.dedup and not args.dry_run:
        _dedup_sources(store, prefer=args.prefer,
                       threshold_m=args.dedup_threshold)

    if args.validate and not args.dry_run:
        _validate_data(store)


if __name__ == "__main__":
    main()
