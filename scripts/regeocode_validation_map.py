#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
regeocode_validation_map.py – Interactive HTML map showing before vs after
coordinates for ANATEL towers that were re-geocoded.

Reads the CSV produced by:
    python load_brazil_towers.py --regeocode --regeocode-report report.csv

Features:
  • Colour-coded markers by snap strategy (same_op / any_op / spiral)
  • Operator filter (dropdown in layer control)
  • Distance labels on each connecting line
  • Before (red) → After (green) movement lines
  • Cluster groups for performance with large datasets
  • Sampled mode to keep the map lightweight (default 2 000 towers)

Usage:
    python scripts/regeocode_validation_map.py report.csv
    python scripts/regeocode_validation_map.py report.csv --sample 5000
    python scripts/regeocode_validation_map.py report.csv --operator Claro
    python scripts/regeocode_validation_map.py report.csv --operator Vivo --strategy same_op
    python scripts/regeocode_validation_map.py report.csv --all   # no sampling
    python scripts/regeocode_validation_map.py report.csv -o map.html
"""

import argparse
import csv
import os
import random
import sys
import webbrowser

try:
    import folium
    from folium.plugins import MarkerCluster
except ImportError:
    sys.exit("folium is required: pip install folium")


# ── Colours per strategy ────────────────────────────────────────
STRATEGY_COLOURS = {
    "same_op": {"line": "#2196F3", "before": "#E53935", "after": "#43A047", "label": "Same-operator snap"},
    "any_op":  {"line": "#FF9800", "before": "#E53935", "after": "#FB8C00", "label": "Any-operator snap"},
    "spiral":  {"line": "#9C27B0", "before": "#E53935", "after": "#7B1FA2", "label": "Spiral placement"},
}

DEFAULT_SAMPLE = 2000


def _load_report(path: str) -> list[dict]:
    """Load the regeocode report CSV into a list of dicts."""
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["old_lat"] = float(row["old_lat"])
                row["old_lon"] = float(row["old_lon"])
                row["new_lat"] = float(row["new_lat"])
                row["new_lon"] = float(row["new_lon"])
                row["snap_dist_m"] = float(row["snap_dist_m"]) if row["snap_dist_m"] else 0.0
            except (ValueError, KeyError):
                continue
            rows.append(row)
    return rows


def _build_map(rows: list[dict], title: str = "Regeocode Validation") -> folium.Map:
    """Build an interactive folium map with operator layers and distance labels."""
    if not rows:
        sys.exit("No rows to display.")

    # Centre on the median point
    lats = [r["new_lat"] for r in rows]
    lons = [r["new_lon"] for r in rows]
    centre = [sorted(lats)[len(lats) // 2], sorted(lons)[len(lons) // 2]]

    m = folium.Map(location=centre, zoom_start=5, tiles="cartodbpositron")

    # ── Build one FeatureGroup per operator ──────────────────────
    operators = sorted({r["operator"] for r in rows})
    op_groups: dict[str, folium.FeatureGroup] = {}
    for op in operators:
        fg = folium.FeatureGroup(name=op, show=True)
        op_groups[op] = fg

    # ── Strategy summary layer (always visible) ─────────────────
    legend_html = _build_legend_html(rows, title)

    for row in rows:
        op = row["operator"]
        strat = row.get("strategy", "spiral")
        colours = STRATEGY_COLOURS.get(strat, STRATEGY_COLOURS["spiral"])
        dist = row["snap_dist_m"]
        ocid_ref = row.get("ocid_ref", "—")

        old = [row["old_lat"], row["old_lon"]]
        new = [row["new_lat"], row["new_lon"]]

        popup_html = (
            f"<b>{row['id']}</b><br>"
            f"Operator: {op}<br>"
            f"Strategy: {strat}<br>"
            f"Snap distance: <b>{dist:.0f} m</b><br>"
            f"OCID ref: {ocid_ref}<br>"
            f"Old: {old[0]:.6f}, {old[1]:.6f}<br>"
            f"New: {new[0]:.6f}, {new[1]:.6f}"
        )

        fg = op_groups[op]

        # Before marker (red circle)
        folium.CircleMarker(
            location=old,
            radius=4,
            color=colours["before"],
            fill=True,
            fill_opacity=0.7,
            popup=folium.Popup(f"<b>BEFORE</b><br>{popup_html}", max_width=300),
            tooltip=f"{row['id']} (before)",
        ).add_to(fg)

        # After marker (green circle)
        folium.CircleMarker(
            location=new,
            radius=5,
            color=colours["after"],
            fill=True,
            fill_opacity=0.9,
            popup=folium.Popup(f"<b>AFTER</b><br>{popup_html}", max_width=300),
            tooltip=f"{row['id']} → {dist:.0f}m ({strat})",
        ).add_to(fg)

        # Movement line
        folium.PolyLine(
            locations=[old, new],
            color=colours["line"],
            weight=2,
            opacity=0.6,
            dash_array="6",
        ).add_to(fg)

        # Distance label at midpoint
        mid = [(old[0] + new[0]) / 2, (old[1] + new[1]) / 2]
        if dist > 0:
            folium.Marker(
                location=mid,
                icon=folium.DivIcon(
                    html=f'<div style="font-size:9px;color:{colours["line"]};'
                         f'font-weight:bold;white-space:nowrap;'
                         f'text-shadow:1px 1px 1px #fff,-1px -1px 1px #fff">'
                         f'{dist:.0f}m</div>',
                    icon_size=(50, 14),
                    icon_anchor=(25, 7),
                ),
            ).add_to(fg)

    # Add all operator groups to map
    for fg in op_groups.values():
        fg.add_to(m)

    # Layer control for operator filtering
    folium.LayerControl(collapsed=False).add_to(m)

    # Legend overlay
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


def _build_legend_html(rows: list[dict], title: str) -> str:
    """Build a floating HTML legend with strategy breakdown and stats."""
    total = len(rows)
    strat_counts = {}
    dists = []
    for r in rows:
        s = r.get("strategy", "spiral")
        strat_counts[s] = strat_counts.get(s, 0) + 1
        if r["snap_dist_m"] > 0:
            dists.append(r["snap_dist_m"])

    dists.sort()
    median_d = dists[len(dists) // 2] if dists else 0
    mean_d = sum(dists) / len(dists) if dists else 0
    p90_d = dists[int(len(dists) * 0.9)] if dists else 0

    op_counts = {}
    for r in rows:
        op_counts[r["operator"]] = op_counts.get(r["operator"], 0) + 1
    top_ops = sorted(op_counts.items(), key=lambda x: -x[1])[:6]

    legend_items = ""
    for strat, info in STRATEGY_COLOURS.items():
        count = strat_counts.get(strat, 0)
        pct = count / total * 100 if total else 0
        legend_items += (
            f'<div style="margin:2px 0">'
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'background:{info["line"]};border-radius:3px;vertical-align:middle;'
            f'margin-right:6px"></span>'
            f'{info["label"]}: <b>{count:,}</b> ({pct:.1f}%)</div>'
        )

    ops_html = "".join(
        f"<div style='margin:1px 0;font-size:11px'>{op}: <b>{c:,}</b></div>"
        for op, c in top_ops
    )

    return f"""
    <div style="
        position: fixed; bottom: 20px; left: 20px; z-index: 9999;
        background: white; padding: 14px 18px; border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.25); font-family: sans-serif;
        font-size: 12px; max-width: 280px; line-height: 1.4;
    ">
        <div style="font-size:14px;font-weight:bold;margin-bottom:8px">
            {title}
        </div>
        <div style="font-size:11px;color:#666;margin-bottom:6px">
            Showing {total:,} towers
        </div>
        {legend_items}
        <hr style="margin:8px 0;border:none;border-top:1px solid #ddd">
        <div style="font-size:11px">
            <b>Snap distance stats</b><br>
            Median: <b>{median_d:.0f}m</b> &nbsp;
            Mean: <b>{mean_d:.0f}m</b> &nbsp;
            P90: <b>{p90_d:.0f}m</b>
        </div>
        <hr style="margin:8px 0;border:none;border-top:1px solid #ddd">
        <div style="font-size:11px"><b>Top operators</b></div>
        {ops_html}
    </div>
    """


def main():
    parser = argparse.ArgumentParser(
        description="Generate an interactive validation map from regeocode report CSV",
    )
    parser.add_argument("csv_file", help="Path to regeocode report CSV")
    parser.add_argument("-o", "--output", default="regeocode_map.html",
                        help="Output HTML file (default: regeocode_map.html)")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE,
                        help=f"Sample N towers for performance (default: {DEFAULT_SAMPLE})")
    parser.add_argument("--all", action="store_true",
                        help="Show all towers (no sampling — may be slow)")
    parser.add_argument("--operator", type=str, default=None,
                        help="Filter to a single operator (e.g. Claro, Vivo, TIM)")
    parser.add_argument("--strategy", type=str, default=None,
                        choices=["same_op", "any_op", "spiral"],
                        help="Filter to a single snap strategy")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open the map in a browser")
    args = parser.parse_args()

    # ── Load & filter ────────────────────────────────────────────
    print(f"Loading {args.csv_file}...")
    rows = _load_report(args.csv_file)
    print(f"  Loaded {len(rows):,} rows")

    if args.operator:
        needle = args.operator.lower()
        rows = [r for r in rows if needle in r["operator"].lower()]
        print(f"  Filtered to operator '{args.operator}': {len(rows):,} rows")

    if args.strategy:
        rows = [r for r in rows if r.get("strategy") == args.strategy]
        print(f"  Filtered to strategy '{args.strategy}': {len(rows):,} rows")

    if not args.all and len(rows) > args.sample:
        random.seed(42)  # reproducible
        rows = random.sample(rows, args.sample)
        print(f"  Sampled down to {len(rows):,} (use --all to show all)")

    if not rows:
        sys.exit("No rows match the filters.")

    # ── Build & save map ─────────────────────────────────────────
    title = "Regeocode Validation"
    if args.operator:
        title += f" — {args.operator}"
    if args.strategy:
        title += f" ({args.strategy})"

    print("Building map...")
    m = _build_map(rows, title=title)
    m.save(args.output)
    size_kb = os.path.getsize(args.output) / 1024
    print(f"  Saved → {args.output} ({size_kb:.0f} KB)")

    if not args.no_open:
        webbrowser.open(f"file://{os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
