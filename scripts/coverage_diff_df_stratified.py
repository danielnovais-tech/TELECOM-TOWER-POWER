#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Coverage-diff stratified analysis: itmlogic vs P.1812 across DF towers by distance bins.

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telecom_tower_power import TerrainService  # noqa: E402
from rf_engines.compare import compare  # noqa: E402

logger = logging.getLogger("coverage_diff_df_stratified")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in km."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Stratified coverage comparison (itmlogic vs P.1812) for DF towers."
    )
    p.add_argument("--towers-csv", type=Path, default=Path("towers_brazil.csv"))
    p.add_argument("--output-json", type=Path, default=Path("artifacts/df_stratified_comparison.json"))
    p.add_argument("--output-md", type=Path, default=Path("artifacts/df_stratified_comparison.md"))
    p.add_argument("--lat-min", type=float, default=-16.10, help="DF bounding box lat min")
    p.add_argument("--lat-max", type=float, default=-15.45)
    p.add_argument("--lon-min", type=float, default=-48.30)
    p.add_argument("--lon-max", type=float, default=-47.30)
    p.add_argument("--rx-lat", type=float, default=-15.7942, help="Brasilia centro")
    p.add_argument("--rx-lon", type=float, default=-47.8825)
    p.add_argument("--freq-hz", type=float, default=850_000_000.0)
    p.add_argument("--htg", type=float, default=35.0, help="Tx antenna height AGL")
    p.add_argument("--hrg", type=float, default=10.0, help="Rx antenna height AGL")
    p.add_argument("--max-towers", type=int, default=10)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Load towers and filter to DF region
    towers: list[dict[str, Any]] = []
    if not args.towers_csv.is_file():
        logger.error("towers_csv not found: %s", args.towers_csv)
        return 1

    with open(args.towers_csv, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            lat = float(row["lat"])
            lon = float(row["lon"])
            if args.lat_min <= lat <= args.lat_max and args.lon_min <= lon <= args.lon_max:
                towers.append({
                    "id": row["id"],
                    "lat": lat,
                    "lon": lon,
                    "height_m": float(row.get("height_m") or 30.0),
                })

    if len(towers) < 3:
        logger.error("Not enough DF towers in dataset: %d", len(towers))
        return 1

    # Select deterministically
    selected = sorted(towers, key=lambda t: t["id"])[: args.max_towers]
    logger.info("Loaded %d towers from DF region; analyzing %d", len(towers), len(selected))

    # Run comparison for each tower
    terrain = TerrainService()
    rows: list[dict[str, Any]] = []

    for i, t in enumerate(selected, 1):
        rx_lat, rx_lon = args.rx_lat, args.rx_lon
        d_km_total = haversine_km(t["lat"], t["lon"], rx_lat, rx_lon)

        # Nudge receiver if distance is too short
        if d_km_total <= 0.08:
            rx_lat = args.rx_lat + 0.01
            rx_lon = args.rx_lon + 0.01
            d_km_total = haversine_km(t["lat"], t["lon"], rx_lat, rx_lon)

        # Fetch terrain profile
        h_m = terrain.profile(t["lat"], t["lon"], rx_lat, rx_lon, num_points=50)
        d_km = [j * d_km_total / 49 for j in range(50)]

        # Run comparison
        try:
            res = compare(
                engine_names=["itmlogic", "itu-p1812"],
                reference="itu-p1812",
                f_hz=args.freq_hz,
                d_km=d_km,
                h_m=h_m,
                htg=max(10.0, t["height_m"]),
                hrg=args.hrg,
                phi_t=t["lat"],
                lam_t=t["lon"],
                phi_r=rx_lat,
                lam_r=rx_lon,
                pol=2,
                zone=4,
            ).to_dict()
        except Exception as exc:
            logger.warning("Comparison failed for tower %s: %s", t["id"], exc)
            continue

        by_engine = {r["engine"]: r for r in res["rows"]}
        itm = by_engine.get("itmlogic", {})
        p18 = by_engine.get("itu-p1812", {})

        rows.append({
            "idx": i,
            "tower_id": t["id"],
            "distance_km": round(d_km_total, 3),
            "itmlogic_db": itm.get("basic_loss_db"),
            "p1812_db": p18.get("basic_loss_db"),
            "delta_db": itm.get("delta_db"),
            "itmlogic_ms": itm.get("runtime_ms"),
            "p1812_ms": p18.get("runtime_ms"),
        })
        logger.info(
            "[%d/%d] tower=%s d=%.3f km delta=%.3f dB",
            i,
            len(selected),
            t["id"],
            d_km_total,
            itm.get("delta_db", 0),
        )

    if not rows:
        logger.error("No valid comparisons produced")
        return 1

    valid = [r for r in rows if r["delta_db"] is not None]
    logger.info("Produced %d rows, %d valid", len(rows), len(valid))

    # Stratify by distance
    strata: dict[str, list[dict[str, Any]]] = {
        "0_5km": [r for r in valid if 0 <= r["distance_km"] < 5],
        "5_10km": [r for r in valid if 5 <= r["distance_km"] < 10],
        "10plus_km": [r for r in valid if r["distance_km"] >= 10],
    }

    # Compute aggregate statistics
    def compute_stats(subset: list[dict[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {}
        deltas = [r["delta_db"] for r in subset]
        abs_deltas = [abs(x) for x in deltas]
        itm_ms = [r["itmlogic_ms"] for r in subset if r["itmlogic_ms"] is not None]
        p18_ms = [r["p1812_ms"] for r in subset if r["p1812_ms"] is not None]
        return {
            "count": len(subset),
            "delta_mean_db": round(statistics.mean(deltas), 3),
            "delta_median_db": round(statistics.median(deltas), 3),
            "delta_stdev_db": round(statistics.stdev(deltas), 3) if len(deltas) > 1 else 0,
            "delta_mae_db": round(statistics.mean(abs_deltas), 3),
            "delta_max_abs_db": round(max(abs_deltas), 3),
            "runtime_mean_ms_itmlogic": round(statistics.mean(itm_ms), 3) if itm_ms else None,
            "runtime_mean_ms_p1812": round(statistics.mean(p18_ms), 3) if p18_ms else None,
        }

    global_stats = compute_stats(valid)
    strata_stats = {name: compute_stats(rows) for name, rows in strata.items()}

    output: dict[str, Any] = {
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "config": {
            "df_bbox": {
                "lat_min": args.lat_min,
                "lat_max": args.lat_max,
                "lon_min": args.lon_min,
                "lon_max": args.lon_max,
            },
            "rx_location": {"lat": args.rx_lat, "lon": args.rx_lon},
            "freq_hz": args.freq_hz,
            "max_towers": args.max_towers,
        },
        "global_stats": global_stats,
        "strata_stats": strata_stats,
        "rows": rows,
    }

    # Write JSON
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Wrote JSON output: %s", args.output_json)

    # Write markdown report
    md_lines = [
        "# DF Coverage Comparison Report: itmlogic vs ITU-R P.1812",
        "",
        f"**Generated:** {output['timestamp']}",
        "",
        "## Executive Summary",
        "",
        f"- **Towers analyzed:** {global_stats.get('count', 0)}",
        f"- **Mean delta:** {global_stats.get('delta_mean_db', 0):.3f} dB (itmlogic - P.1812)",
        f"- **Median delta:** {global_stats.get('delta_median_db', 0):.3f} dB",
        f"- **MAE delta:** {global_stats.get('delta_mae_db', 0):.3f} dB",
        f"- **Max abs delta:** {global_stats.get('delta_max_abs_db', 0):.3f} dB",
        "",
        "## Interpretation",
        "",
        "Positive delta → **itmlogic is more pessimistic** (predicts higher path loss)",
        "",
        "Negative delta → **itmlogic is more optimistic** (predicts lower path loss)",
        "",
        "## Performance",
        "",
        f"- **itmlogic avg runtime:** {global_stats.get('runtime_mean_ms_itmlogic', 0):.3f} ms",
        f"- **P.1812 avg runtime:** {global_stats.get('runtime_mean_ms_p1812', 0):.3f} ms",
        f"- **Speedup:** {global_stats.get('runtime_mean_ms_p1812', 0) / max(global_stats.get('runtime_mean_ms_itmlogic', 1), 0.001):.1f}x",
        "",
        "## Distance-Stratified Analysis",
        "",
    ]

    for bin_name in ["0_5km", "5_10km", "10plus_km"]:
        stats = strata_stats.get(bin_name, {})
        count = stats.get("count", 0)
        if count == 0:
            continue
        bin_display = bin_name.replace("_", " ").replace("km", " km")
        md_lines.extend([
            f"### {bin_display}",
            "",
            f"- **Count:** {count}",
            f"- **Mean delta:** {stats.get('delta_mean_db', 0):.3f} dB",
            f"- **Median delta:** {stats.get('delta_median_db', 0):.3f} dB",
            f"- **Stdev:** {stats.get('delta_stdev_db', 0):.3f} dB",
            f"- **MAE:** {stats.get('delta_mae_db', 0):.3f} dB",
            f"- **Max abs delta:** {stats.get('delta_max_abs_db', 0):.3f} dB",
            "",
        ])

    md_lines.extend([
        "## Detailed Results (All Towers)",
        "",
        "| Index | Tower ID | Distance (km) | itmlogic (dB) | P.1812 (dB) | Delta (dB) | itmlogic (ms) | P.1812 (ms) |",
        "|-------|----------|---------------|---------------|-------------|------------|---------------|------------|",
    ])

    for r in sorted(rows, key=lambda x: x["distance_km"]):
        md_lines.append(
            f"| {r['idx']:2d} | {r['tower_id']:12s} | {r['distance_km']:6.3f} | "
            f"{r['itmlogic_db']:13.3f} | {r['p1812_db']:11.3f} | "
            f"{r['delta_db']:10.3f} | {r['itmlogic_ms']:13.3f} | {r['p1812_ms']:10.3f} |"
        )

    md_text = "\n".join(md_lines)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(md_text)
    logger.info("Wrote markdown report: %s", args.output_md)

    return 0


if __name__ == "__main__":
    sys.exit(main())
