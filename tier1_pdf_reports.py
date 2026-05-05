# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
"""Professional Tier-1 PDF reports for coverage + interference endpoints."""

from __future__ import annotations

import html
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence


def _escape(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (dict, list, tuple)):
        return html.escape(json.dumps(value, ensure_ascii=True, indent=2))
    return html.escape(str(value))


def _labelize(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _table_from_mapping(title: str, data: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><th>{_escape(_labelize(key))}</th><td>{_escape(value)}</td></tr>"
        for key, value in data.items()
    )
    return f"<section class='section'><h2>{_escape(title)}</h2><table>{rows}</table></section>"


def _table_from_rows(title: str, rows: Sequence[Mapping[str, Any]], *, limit: int = 20) -> str:
    if not rows:
        return ""
    shown = list(rows[:limit])
    columns: List[str] = []
    for row in shown:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    head = "".join(f"<th>{_escape(_labelize(col))}</th>" for col in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_escape(row.get(col))}</td>" for col in columns) + "</tr>"
        for row in shown
    )
    suffix = ""
    if len(rows) > limit:
        suffix = (
            f"<p class='muted'>Showing first {limit} rows of {len(rows)} total "
            f"to keep the PDF concise.</p>"
        )
    return (
        f"<section class='section'><h2>{_escape(title)}</h2>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{suffix}</section>"
    )


def _cards(cards: Iterable[tuple[str, Any]]) -> str:
    card_html = "".join(
        "<div class='card'>"
        f"<div class='card-label'>{_escape(label)}</div>"
        f"<div class='card-value'>{_escape(value)}</div>"
        "</div>"
        for label, value in cards
    )
    return f"<section class='cards'>{card_html}</section>"


def _render_html(title: str, subtitle: str, cards: Iterable[tuple[str, Any]], sections: Sequence[str]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""
<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8'>
  <style>
    @page {{ size: A4; margin: 16mm; }}
    body {{ font-family: DejaVu Sans, Arial, sans-serif; color: #10233d; font-size: 10.5pt; line-height: 1.45; }}
    .hero {{ background: linear-gradient(135deg, #0f2744, #1d5f86); color: white; padding: 18px 22px; border-radius: 14px; margin-bottom: 16px; }}
    .eyebrow {{ text-transform: uppercase; letter-spacing: 0.12em; font-size: 8pt; opacity: 0.82; margin-bottom: 6px; }}
    h1 {{ margin: 0; font-size: 20pt; }}
    .subtitle {{ margin-top: 6px; font-size: 10pt; opacity: 0.92; }}
    .generated {{ margin-top: 10px; font-size: 8.5pt; opacity: 0.78; }}
    .cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; margin: 16px 0; }}
    .card {{ background: #f4f7fb; border: 1px solid #d6e0ea; border-radius: 10px; padding: 10px 12px; }}
    .card-label {{ color: #47627c; font-size: 8pt; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }}
    .card-value {{ color: #0f2744; font-size: 14pt; font-weight: 700; }}
    .section {{ margin-top: 14px; }}
    h2 {{ color: #123557; font-size: 11.5pt; margin: 0 0 8px; padding-bottom: 4px; border-bottom: 1px solid #d6e0ea; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 4px; }}
    th, td {{ border: 1px solid #d6e0ea; padding: 6px 8px; vertical-align: top; }}
    th {{ background: #edf3f8; text-align: left; width: 32%; color: #284b69; }}
    thead th {{ background: #dfeaf3; width: auto; }}
    tbody tr:nth-child(even) td {{ background: #fbfdff; }}
    .muted {{ color: #60758d; font-size: 8.5pt; margin-top: 6px; }}
    pre {{ white-space: pre-wrap; background: #f7f9fb; border: 1px solid #d6e0ea; border-radius: 8px; padding: 10px; font-size: 8.5pt; }}
  </style>
</head>
<body>
  <section class='hero'>
    <div class='eyebrow'>TELECOM TOWER POWER • Tier-1 Engineering Report</div>
    <h1>{_escape(title)}</h1>
    <div class='subtitle'>{_escape(subtitle)}</div>
    <div class='generated'>Generated {generated}</div>
  </section>
  {_cards(cards)}
  {''.join(sections)}
</body>
</html>
"""


def _write_pdf(html_doc: str) -> io.BytesIO:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover - exercised through endpoint path
        raise RuntimeError(
            "WeasyPrint is unavailable. Install the Python package and Cairo/Pango system libraries."
        ) from exc
    pdf_bytes = HTML(string=html_doc).write_pdf()
    return io.BytesIO(pdf_bytes)


def render_coverage_predict_pdf(request_body: Mapping[str, Any], response_body: Mapping[str, Any]) -> io.BytesIO:
    mode = str(response_body.get("mode") or request_body.get("bbox") and "grid" or "point")
    engine = response_body.get("engine_used", request_body.get("engine", "auto"))
    if response_body.get("status") == "queued":
        cards = [
            ("Status", response_body.get("status", "queued")),
            ("Engine", engine),
            ("Job ID", response_body.get("job_id", "-")),
            ("Tier", request_body.get("tier", "tier-1")),
        ]
        sections = [
            _table_from_mapping("Request Summary", {
                "scene_s3_uri": request_body.get("scene_s3_uri"),
                "bbox": request_body.get("bbox"),
                "grid_size": request_body.get("grid_size"),
                "tx_lat": request_body.get("tx_lat"),
                "tx_lon": request_body.get("tx_lon"),
                "tx_height_m": request_body.get("tx_height_m"),
                "band": request_body.get("band"),
            }),
            _table_from_mapping("Job Tracking", {
                "poll_url": response_body.get("poll_url"),
                "result_s3_uri": response_body.get("result_s3_uri"),
            }),
        ]
        html_doc = _render_html(
            "Coverage Prediction Report",
            "Ray-tracing job accepted and queued for GPU processing.",
            cards,
            sections,
        )
        return _write_pdf(html_doc)

    cards = [
        ("Mode", mode),
        ("Engine", engine),
        ("Signal", response_body.get("signal_dbm", response_body.get("signal_mean_dbm", "-"))),
        ("Feasible", response_body.get("feasible", response_body.get("feasible_coverage_pct", "-"))),
    ]
    sections = [
        _table_from_mapping("Request Parameters", {
            "tx_lat": request_body.get("tx_lat"),
            "tx_lon": request_body.get("tx_lon"),
            "tx_height_m": request_body.get("tx_height_m"),
            "tx_power_dbm": request_body.get("tx_power_dbm"),
            "band": request_body.get("band"),
            "rx_lat": request_body.get("rx_lat"),
            "rx_lon": request_body.get("rx_lon"),
            "rx_height_m": request_body.get("rx_height_m"),
            "bbox": request_body.get("bbox"),
            "grid_size": response_body.get("grid_size", request_body.get("grid_size")),
        }),
        _table_from_mapping("Prediction Summary", {
            "model_source": response_body.get("model_source"),
            "model_version": response_body.get("model_version"),
            "distance_km": response_body.get("distance_km"),
            "signal_dbm": response_body.get("signal_dbm"),
            "signal_min_dbm": response_body.get("signal_min_dbm"),
            "signal_max_dbm": response_body.get("signal_max_dbm"),
            "signal_mean_dbm": response_body.get("signal_mean_dbm"),
            "feasible": response_body.get("feasible"),
            "feasible_coverage_pct": response_body.get("feasible_coverage_pct"),
            "confidence": response_body.get("confidence"),
            "clutter_class": response_body.get("clutter_class"),
            "clutter_label": response_body.get("clutter_label"),
        }),
    ]
    points = response_body.get("points")
    if isinstance(points, list) and points:
        sections.append(_table_from_rows("Grid Sample", points, limit=30))
    features = response_body.get("features")
    if isinstance(features, dict) and features:
        sections.append(_table_from_mapping("Model Features", features))
    explanation = response_body.get("explanation")
    if explanation:
        sections.append(f"<section class='section'><h2>Explanation</h2><pre>{_escape(explanation)}</pre></section>")
    html_doc = _render_html(
        "Coverage Prediction Report",
        "Professional planning output for /coverage/predict.",
        cards,
        sections,
    )
    return _write_pdf(html_doc)


def render_interference_pdf(request_body: Mapping[str, Any], response_body: Mapping[str, Any]) -> io.BytesIO:
    victim = response_body.get("victim", {})
    cards = [
        ("Engine", response_body.get("engine")),
        ("Aggregate I", response_body.get("aggregate_i_dbm")),
        ("I/N", response_body.get("i_over_n_db")),
        ("SINR", response_body.get("sinr_db")),
    ]
    sections = [
        _table_from_mapping("Victim Receiver", dict(victim) if isinstance(victim, dict) else {}),
        _table_from_mapping("Study Summary", {
            "search_radius_km": request_body.get("search_radius_km"),
            "aggressor_plmn": request_body.get("aggressor_plmn"),
            "n_candidates": response_body.get("n_candidates"),
            "n_in_radius": response_body.get("n_in_radius"),
            "n_contributing": response_body.get("n_contributing"),
            "co_channel_count": response_body.get("co_channel_count"),
            "adjacent_channel_count": response_body.get("adjacent_channel_count"),
            "n_filtered_by_plmn": response_body.get("n_filtered_by_plmn"),
            "noise_dbm": response_body.get("noise_dbm"),
        }),
    ]
    top = response_body.get("top_n_aggressors")
    if isinstance(top, list) and top:
        sections.append(_table_from_rows("Top Aggressors", top, limit=20))
    agg_op = response_body.get("aggregate_by_operator_dbm")
    if isinstance(agg_op, dict) and agg_op:
        sections.append(_table_from_rows(
            "Aggregate By Operator",
            [{"operator": k, "aggregate_i_dbm": v} for k, v in agg_op.items()],
            limit=20,
        ))
    agg_plmn = response_body.get("aggregate_by_plmn_dbm")
    if isinstance(agg_plmn, dict) and agg_plmn:
        sections.append(_table_from_rows(
            "Aggregate By PLMN",
            [{"plmn": k, "aggregate_i_dbm": v} for k, v in agg_plmn.items()],
            limit=20,
        ))
    html_doc = _render_html(
        "Interference Analysis Report",
        "Professional engineering output for /coverage/interference.",
        cards,
        sections,
    )
    return _write_pdf(html_doc)
