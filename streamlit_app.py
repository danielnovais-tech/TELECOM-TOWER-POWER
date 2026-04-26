"""
streamlit_app.py
Streamlit Community Cloud frontend for TELECOM TOWER POWER.

Uses the standalone sync engine (telecom_tower_power.py) for map /
analysis, and the FastAPI backend for batch jobs & rate-limit display.
"""

import csv
import io
import math
import os
import time

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

from telecom_tower_power import (
    Band,
    LinkEngine,
    LinkResult,
    Receiver,
    TelecomTowerPower,
    Tower,
)

# ── Page config ─────────────────────────────────────────────
st.set_page_config(
    page_title="TELECOM TOWER POWER",
    page_icon="📡",
    layout="wide",
)

BAND_MAP = {
    "700MHz": Band.BAND_700,
    "1800MHz": Band.BAND_1800,
    "2600MHz": Band.BAND_2600,
    "3500MHz": Band.BAND_3500,
}

SRTM_DIR = os.getenv("SRTM_DATA_DIR", "./srtm_data")
API_URL = os.getenv("API_URL", "http://localhost:8000")


# ── API client helpers ──────────────────────────────────────
def _api_headers() -> dict:
    """Return headers with the configured API key."""
    key = st.session_state.get("api_key", "")
    return {"X-API-Key": key} if key else {}


def _update_rate_limit(resp: requests.Response) -> None:
    """Store rate-limit headers in session state for sidebar display."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    limit = resp.headers.get("X-RateLimit-Limit")
    if remaining is not None:
        st.session_state["rl_remaining"] = int(remaining)
        st.session_state["rl_limit"] = int(limit or 0)


def _handle_api_error(resp: requests.Response) -> bool:
    """Show user-friendly messages for common errors. Returns True if error."""
    if resp.status_code == 429:
        detail = resp.json().get("detail", "Rate limit exceeded.")
        st.error(f"⏳ **Rate limit exceeded** — {detail}")
        return True
    if resp.status_code == 403:
        detail = resp.json().get("detail", "Forbidden.")
        st.error(f"🔒 **Access denied** — {detail}")
        return True
    if resp.status_code == 401:
        st.error("🔑 **Invalid API key** — check your key in the sidebar.")
        return True
    if resp.status_code >= 400:
        detail = resp.json().get("detail", resp.text)
        st.error(f"❌ **Error {resp.status_code}** — {detail}")
        return True
    return False


def api_get(path: str, **kwargs) -> requests.Response | None:
    """GET helper with error handling and rate-limit tracking."""
    try:
        resp = requests.get(f"{API_URL}{path}", headers=_api_headers(), timeout=30, **kwargs)
        _update_rate_limit(resp)
        if _handle_api_error(resp):
            return None
        return resp
    except requests.ConnectionError:
        st.error("🔌 **Cannot reach API** — is the backend running?")
        return None


def api_post(path: str, **kwargs) -> requests.Response | None:
    """POST helper with error handling and rate-limit tracking."""
    try:
        resp = requests.post(f"{API_URL}{path}", headers=_api_headers(), timeout=120, **kwargs)
        _update_rate_limit(resp)
        if _handle_api_error(resp):
            return None
        return resp
    except requests.ConnectionError:
        st.error("🔌 **Cannot reach API** — is the backend running?")
        return None


@st.cache_data(ttl=300, show_spinner="Loading towers from API…")
def fetch_tower_list_cached(api_key: str) -> list[dict] | None:
    """Fetch towers from the API, cached for 5 minutes."""
    try:
        resp = requests.get(
            f"{API_URL}/towers",
            headers={"X-API-Key": api_key} if api_key else {},
            params={"limit": 500},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("towers", [])
    except requests.ConnectionError:
        pass
    return None


# ── Helpers ─────────────────────────────────────────────────
@st.cache_resource
def get_engine() -> TelecomTowerPower:
    return TelecomTowerPower(srtm_dir=SRTM_DIR)


def load_towers_from_csv(engine: TelecomTowerPower, csv_path: str):
    """Load towers from the bundled CSV."""
    if not os.path.isfile(csv_path):
        return
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bands_raw = row["bands"].replace('"', "").split(",")
            bands = [BAND_MAP[b.strip()] for b in bands_raw if b.strip() in BAND_MAP]
            if not bands:
                continue
            tower = Tower(
                id=row["id"].strip(),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                height_m=float(row["height_m"]),
                operator=row["operator"].strip(),
                bands=bands,
                power_dbm=float(row["power_dbm"]),
            )
            engine.add_tower(tower)


def ensure_towers_loaded(engine: TelecomTowerPower):
    if not engine.towers:
        load_towers_from_csv(engine, "towers_brazil.csv")


def format_band(band: Band) -> str:
    return {
        Band.BAND_700: "700 MHz",
        Band.BAND_1800: "1800 MHz",
        Band.BAND_2600: "2600 MHz",
        Band.BAND_3500: "3500 MHz",
    }.get(band, str(band))


def build_map(
    engine: TelecomTowerPower,
    receiver: Receiver | None = None,
    link_result: LinkResult | None = None,
    selected_tower: Tower | None = None,
    repeater_chain: list[Tower] | None = None,
) -> folium.Map:
    """Build a Folium map with towers, receiver, and link lines."""
    towers = list(engine.towers.values())
    if towers:
        center_lat = sum(t.lat for t in towers) / len(towers)
        center_lon = sum(t.lon for t in towers) / len(towers)
    else:
        center_lat, center_lon = -15.83, -47.90

    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles="CartoDB dark_matter")

    # Tower markers
    for t in towers:
        color = "red" if selected_tower and t.id == selected_tower.id else "blue"
        folium.Marker(
            [t.lat, t.lon],
            tooltip=f"{t.id} ({t.operator})",
            popup=f"<b>{t.id}</b><br>{t.operator}<br>{t.height_m}m<br>{', '.join(format_band(b) for b in t.bands)}<br>{t.power_dbm} dBm",
            icon=folium.Icon(color=color, icon="signal", prefix="fa"),
        ).add_to(m)

    # Receiver marker
    if receiver:
        folium.Marker(
            [receiver.lat, receiver.lon],
            tooltip="Receiver",
            popup=f"<b>Receiver</b><br>{receiver.height_m}m<br>{receiver.antenna_gain_dbi} dBi",
            icon=folium.Icon(color="green", icon="home", prefix="fa"),
        ).add_to(m)

    # Link line
    if selected_tower and receiver and link_result:
        line_color = "lime" if link_result.feasible else "red"
        folium.PolyLine(
            [[selected_tower.lat, selected_tower.lon], [receiver.lat, receiver.lon]],
            color=line_color,
            weight=3,
            opacity=0.8,
            tooltip=f"{link_result.signal_dbm:.1f} dBm | {link_result.distance_km:.2f} km",
        ).add_to(m)

    # Repeater chain
    if repeater_chain and len(repeater_chain) > 1:
        for i, hop in enumerate(repeater_chain):
            if hop.id.startswith("candidate_"):
                folium.Marker(
                    [hop.lat, hop.lon],
                    tooltip=f"Repeater {i}",
                    icon=folium.Icon(color="orange", icon="broadcast-tower", prefix="fa"),
                ).add_to(m)
        coords = [[hop.lat, hop.lon] for hop in repeater_chain]
        if receiver:
            coords.append([receiver.lat, receiver.lon])
        folium.PolyLine(coords, color="gold", weight=3, dash_array="8").add_to(m)

    return m


# ── Sidebar ─────────────────────────────────────────────────
def sidebar(engine: TelecomTowerPower):
    st.sidebar.title("📡 TELECOM TOWER POWER")
    st.sidebar.markdown("---")

    # ── API key input ───────────────────────────────────────
    st.sidebar.subheader("API connection")
    api_key = st.sidebar.text_input(
        "API key", type="password", key="api_key",
        help="Required for batch jobs. Get one from the signup page.",
    )

    # ── Rate limit display ──────────────────────────────────
    rl_remaining = st.session_state.get("rl_remaining")
    rl_limit = st.session_state.get("rl_limit")
    if rl_remaining is not None and rl_limit is not None:
        st.sidebar.metric(
            "API calls remaining this minute",
            f"{rl_remaining} / {rl_limit}",
        )
    elif api_key:
        st.sidebar.caption("Rate limit info appears after your first API call.")

    st.sidebar.markdown("---")

    # Upload custom towers CSV
    uploaded = st.sidebar.file_uploader("Upload towers CSV", type=["csv"], key="tower_csv")
    if uploaded is not None:
        text = uploaded.getvalue().decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            bands_raw = row["bands"].replace('"', "").split(",")
            bands = [BAND_MAP[b.strip()] for b in bands_raw if b.strip() in BAND_MAP]
            if not bands:
                continue
            tower = Tower(
                id=row["id"].strip(),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                height_m=float(row["height_m"]),
                operator=row["operator"].strip(),
                bands=bands,
                power_dbm=float(row["power_dbm"]),
            )
            engine.add_tower(tower)
            count += 1
        st.sidebar.success(f"Loaded {count} towers from upload")

    # ── Sync tower list from API ────────────────────────────
    if api_key and st.sidebar.button("🔄 Sync towers from API", key="sync_towers"):
        api_towers = fetch_tower_list_cached(api_key)
        if api_towers:
            loaded = 0
            for t in api_towers:
                bands_raw = t.get("bands", [])
                bands = []
                for b in bands_raw:
                    try:
                        bands.append(Band(b))
                    except ValueError:
                        pass
                if not bands:
                    continue
                tower = Tower(
                    id=t["id"], lat=t["lat"], lon=t["lon"],
                    height_m=t["height_m"], operator=t["operator"],
                    bands=bands, power_dbm=t.get("power_dbm", 43.0),
                )
                engine.add_tower(tower)
                loaded += 1
            st.sidebar.success(f"Synced {loaded} towers from API")
        else:
            st.sidebar.warning("Could not fetch towers from API.")

    st.sidebar.markdown("---")
    st.sidebar.subheader("Tower selection")

    tower_ids = sorted(engine.towers.keys())
    if not tower_ids:
        st.sidebar.warning("No towers loaded.")
        return None, None, {}

    selected_id = st.sidebar.selectbox("Select tower", tower_ids)
    tower = engine.towers[selected_id]

    with st.sidebar.expander("Tower details", expanded=False):
        st.write(f"**Operator:** {tower.operator}")
        st.write(f"**Height:** {tower.height_m} m")
        st.write(f"**Bands:** {', '.join(format_band(b) for b in tower.bands)}")
        st.write(f"**Power:** {tower.power_dbm} dBm")
        st.write(f"**Location:** ({tower.lat:.4f}, {tower.lon:.4f})")

    st.sidebar.markdown("---")
    st.sidebar.subheader("Receiver")

    rx_lat = st.sidebar.number_input("Latitude", value=-15.8500, format="%.4f", key="rx_lat")
    rx_lon = st.sidebar.number_input("Longitude", value=-47.8800, format="%.4f", key="rx_lon")
    rx_height = st.sidebar.slider("Antenna height (m)", 1, 60, 10, key="rx_h")
    rx_gain = st.sidebar.slider("Antenna gain (dBi)", 0, 30, 12, key="rx_g")

    receiver = Receiver(lat=rx_lat, lon=rx_lon, height_m=rx_height, antenna_gain_dbi=rx_gain)

    st.sidebar.markdown("---")
    st.sidebar.subheader("Repeater planner")
    max_hops = st.sidebar.slider("Max hops", 1, 5, 3, key="max_hops")

    actions = {
        "analyze": st.sidebar.button("🔍 Analyze link", use_container_width=True),
        "repeater": st.sidebar.button("🗼 Plan repeater chain", use_container_width=True),
    }

    return tower, receiver, actions


# ── Batch analysis (API-backed) ─────────────────────────────
def batch_tab(engine: TelecomTowerPower):
    st.subheader("Batch link analysis")
    api_key = st.session_state.get("api_key", "")

    tower_ids = sorted(engine.towers.keys())
    if not tower_ids:
        st.warning("No towers loaded.")
        return

    # ── Initialise job history list ─────────────────────────
    if "batch_jobs" not in st.session_state:
        st.session_state["batch_jobs"] = []  # list of {job_id, tower_id, status, submitted_at}

    # ── Operator / region filters ───────────────────────────
    operators = sorted({engine.towers[tid].operator for tid in tower_ids})
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        filter_operator = st.selectbox(
            "Filter by operator", ["All"] + operators, key="batch_op_filter"
        )
    with col_f2:
        filter_region = st.text_input(
            "Filter tower ID (contains)", "", key="batch_region_filter",
            help="Substring match on tower ID, e.g. 'BSB' for Brasília towers",
        )

    filtered_ids = tower_ids
    if filter_operator != "All":
        filtered_ids = [
            tid for tid in filtered_ids
            if engine.towers[tid].operator == filter_operator
        ]
    if filter_region:
        q = filter_region.upper()
        filtered_ids = [tid for tid in filtered_ids if q in tid.upper()]

    if not filtered_ids:
        st.info("No towers match the current filters.")
        return

    # ── Submission form ─────────────────────────────────────
    st.markdown("Upload a CSV with columns: `lat`, `lon` (and optionally `height`, `gain`)")
    col1, col2 = st.columns([1, 2])
    with col1:
        batch_tower_id = st.selectbox("Tower for batch", filtered_ids, key="batch_tower")
        batch_file = st.file_uploader("Receivers CSV", type=["csv"], key="batch_csv")

    if batch_file is not None and st.button("🚀 Submit batch job", key="run_batch"):
        if not api_key:
            st.error("🔑 Enter an API key in the sidebar to submit batch jobs.")
            return

        resp = api_post(
            "/batch_submit",
            params={"tower_id": batch_tower_id},
            files={"csv_file": ("receivers.csv", batch_file.getvalue(), "text/csv")},
        )
        if resp is None:
            return

        data = resp.json()
        job_id = data.get("job_id")
        if job_id:
            job_entry = {
                "job_id": job_id,
                "tower_id": batch_tower_id,
                "operator": engine.towers[batch_tower_id].operator,
                "status": "queued",
                "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            st.session_state["batch_jobs"].insert(0, job_entry)
            st.info(f"📋 Job **{job_id}** queued.")

    # ── Auto-refresh toggle ─────────────────────────────────
    jobs = st.session_state.get("batch_jobs", [])
    if not jobs:
        st.caption("No batch jobs submitted yet.")
        return

    st.markdown("---")
    st.subheader("Batch jobs dashboard")

    col_r1, col_r2 = st.columns([1, 3])
    with col_r1:
        auto_refresh = st.checkbox("Auto-refresh (5 s)", value=False, key="batch_auto")
    with col_r2:
        if st.button("🔄 Refresh all jobs", key="refresh_all_jobs"):
            pass  # button press triggers re-run

    # ── Filter jobs by operator ────────────────────────────
    job_filter_op = st.selectbox(
        "Filter jobs by operator",
        ["All"] + sorted({j["operator"] for j in jobs}),
        key="job_filter_op",
    )
    display_jobs = jobs if job_filter_op == "All" else [
        j for j in jobs if j["operator"] == job_filter_op
    ]

    # ── Poll & display each job ────────────────────────────
    for i, job in enumerate(display_jobs):
        job_id = job["job_id"]
        resp = api_get(f"/batch_status/{job_id}")
        if resp is not None:
            job_data = resp.json()
            job["status"] = job_data.get("status", job["status"])

        status = job["status"]
        icon = {"queued": "⏳", "completed": "✅", "failed": "❌"}.get(status, "🔄")
        progress = {"queued": 0.0, "completed": 1.0, "failed": 0.0}.get(status, 0.5)

        with st.expander(
            f"{icon} {job_id}  —  {job['tower_id']} ({job['operator']})  [{status}]",
            expanded=(i == 0),
        ):
            cols = st.columns([2, 1, 1])
            cols[0].write(f"**Tower:** {job['tower_id']}")
            cols[1].write(f"**Operator:** {job['operator']}")
            cols[2].write(f"**Submitted:** {job['submitted_at']}")
            st.progress(progress, text=f"Status: {status}")

            if status == "completed":
                dl_resp = api_get(f"/batch_download/{job_id}")
                if dl_resp is not None:
                    st.download_button(
                        "📥 Download ZIP",
                        data=dl_resp.content,
                        file_name=f"batch_reports_{job['tower_id']}.zip",
                        mime="application/zip",
                        key=f"dl_{job_id}",
                    )
            elif status == "failed":
                error_msg = job_data.get("error", "Unknown error") if resp else "Unknown"
                st.error(f"Error: {error_msg}")

    # ── Summary metrics ─────────────────────────────────────
    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total jobs", len(jobs))
    m2.metric("Queued", sum(1 for j in jobs if j["status"] == "queued"))
    m3.metric("Completed", sum(1 for j in jobs if j["status"] == "completed"))
    m4.metric("Failed", sum(1 for j in jobs if j["status"] == "failed"))

    # ── Auto-refresh via rerun ──────────────────────────────
    if auto_refresh and any(j["status"] == "queued" for j in jobs):
        time.sleep(5)
        st.rerun()


# ── Nearest towers ──────────────────────────────────────────
def nearest_tab(engine: TelecomTowerPower):
    st.subheader("Find nearest towers")
    c1, c2, c3 = st.columns(3)
    with c1:
        n_lat = st.number_input("Latitude", value=-15.8300, format="%.4f", key="n_lat")
    with c2:
        n_lon = st.number_input("Longitude", value=-47.9000, format="%.4f", key="n_lon")
    with c3:
        n_limit = st.number_input("Max results", value=5, min_value=1, max_value=20, key="n_lim")

    if st.button("Search", key="nearest_btn"):
        nearest = engine.find_nearest_towers(n_lat, n_lon, limit=n_limit)
        if nearest:
            rows = []
            for t in nearest:
                d = LinkEngine.haversine_km(n_lat, n_lon, t.lat, t.lon)
                rows.append({
                    "ID": t.id,
                    "Operator": t.operator,
                    "Distance (km)": round(d, 2),
                    "Height (m)": t.height_m,
                    "Bands": ", ".join(format_band(b) for b in t.bands),
                    "Power (dBm)": t.power_dbm,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        else:
            st.info("No towers found.")


# ── SRTM Tiles tab (Enterprise) ─────────────────────────────
# Lets ops/enterprise users inspect SRTM tile coverage for a country
# and trigger a background prefetch. Calls /srtm/status/{country}
# (GET) and /srtm/prefetch (POST) — both require Tier.ENTERPRISE.
_SRTM_KNOWN_COUNTRIES = ["BR", "AR", "CL", "CO", "PE", "MX", "US"]


def srtm_tab() -> None:
    st.subheader("🌍 SRTM Tile Management")
    st.caption(
        "Inspect SRTM tile coverage and pre-download tiles for a country. "
        "**Enterprise tier required.** Prefetch runs as a background job — "
        "the status view auto-counts cached vs. missing tiles."
    )

    if not st.session_state.get("api_key"):
        st.info("Set an Enterprise API key in the sidebar to use this tab.")
        return

    col_a, col_b = st.columns([2, 1])
    with col_a:
        country = st.selectbox(
            "Country (ISO 3166-1 alpha-2)",
            options=_SRTM_KNOWN_COUNTRIES,
            index=0,
            help="Only countries with a defined bounding box appear here.",
        )
        custom = st.text_input(
            "…or enter a custom 2-letter code",
            value="",
            max_chars=2,
            help="Leave empty to use the dropdown selection.",
        ).strip().upper()
        code = custom if len(custom) == 2 else country
    with col_b:
        st.write("")
        st.write("")
        refresh = st.button("🔄 Refresh status", use_container_width=True)
        prefetch = st.button("⬇️ Start prefetch", use_container_width=True, type="primary")

    if prefetch:
        resp = api_post("/srtm/prefetch", json={"country": code})
        if resp is not None and resp.status_code == 200:
            st.success(f"Prefetch started for {code}. Use 'Refresh status' to track progress.")
        elif resp is not None:
            st.error(f"Failed to start prefetch ({resp.status_code}): {resp.text[:300]}")

    # Always render the latest status (refresh button just re-runs the page).
    resp = api_get(f"/srtm/status/{code}")
    if resp is None:
        return
    if resp.status_code == 403:
        st.error("🔒 This endpoint requires an **Enterprise** API key.")
        return
    if resp.status_code == 404:
        st.warning(f"No bounding box defined for `{code}`. Try one of: {', '.join(_SRTM_KNOWN_COUNTRIES)}")
        return
    if resp.status_code != 200:
        st.error(f"Status request failed ({resp.status_code}): {resp.text[:300]}")
        return

    data = resp.json() if resp.content else {}
    total = int(data.get("total_tiles") or data.get("total") or 0)
    cached = int(data.get("cached_tiles") or data.get("cached") or 0)
    missing = int(data.get("missing_tiles") or data.get("missing") or max(total - cached, 0))
    pct = (cached / total) if total > 0 else 0.0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total tiles", f"{total:,}")
    m2.metric("Cached", f"{cached:,}")
    m3.metric("Missing", f"{missing:,}")
    m4.metric("Coverage", f"{pct * 100:.1f}%")
    st.progress(min(max(pct, 0.0), 1.0))

    bounds = data.get("bounds")
    if bounds:
        st.caption(f"Bounding box: `{bounds}`")
    if "missing_list" in data and isinstance(data["missing_list"], list) and data["missing_list"]:
        with st.expander(f"Show first {min(50, len(data['missing_list']))} missing tile names"):
            st.code("\n".join(data["missing_list"][:50]))

    if refresh:
        st.toast("Status refreshed.", icon="✅")


# ── Main ────────────────────────────────────────────────────
def main():
    engine = get_engine()
    ensure_towers_loaded(engine)

    tab_map, tab_batch, tab_nearest, tab_srtm, tab_about = st.tabs(
        ["🗺️ Map & Analysis", "📊 Batch Analysis", "📍 Nearest Towers", "🌍 SRTM Tiles", "ℹ️ About"]
    )

    # ── Map & Analysis tab ──────────────────────────────────
    with tab_map:
        tower, receiver, actions = sidebar(engine)

        link_result = None
        repeater_chain = None

        if tower and receiver and actions.get("analyze"):
            with st.spinner("Analyzing link…"):
                link_result = engine.analyze_link(tower, receiver)
            st.session_state["link_result"] = link_result
            st.session_state["repeater_chain"] = None

        if tower and receiver and actions.get("repeater"):
            max_hops = st.session_state.get("max_hops", 3)
            with st.spinner("Planning repeater chain…"):
                repeater_chain = engine.plan_repeater_chain(tower, receiver, max_hops=max_hops)
            st.session_state["repeater_chain"] = repeater_chain
            # Also compute final-hop link
            if repeater_chain:
                last_hop = repeater_chain[-1]
                link_result = engine.analyze_link(last_hop, receiver)
                st.session_state["link_result"] = link_result

        # Retrieve from session state for re-renders
        link_result = st.session_state.get("link_result")
        repeater_chain = st.session_state.get("repeater_chain")

        col_map, col_info = st.columns([3, 1])

        with col_map:
            m = build_map(engine, receiver, link_result, tower, repeater_chain)
            st_folium(m, use_container_width=True, height=600)

        with col_info:
            if link_result:
                st.subheader("Link analysis")
                st.metric("Distance", f"{link_result.distance_km:.2f} km")
                st.metric("Signal", f"{link_result.signal_dbm:.1f} dBm")
                st.metric("Fresnel clearance", f"{link_result.fresnel_clearance:.2f}")

                if link_result.feasible:
                    st.success("Link is feasible ✅")
                else:
                    st.error("Link is NOT feasible ❌")

                if link_result.los_ok:
                    st.info("Line of sight: Clear")
                else:
                    st.warning("Line of sight: Obstructed")

                st.markdown(f"**Recommendation:** {link_result.recommendation}")

            if repeater_chain and len(repeater_chain) > 1:
                st.subheader("Repeater chain")
                for i, hop in enumerate(repeater_chain):
                    label = "Source" if i == 0 else f"Hop {i}"
                    st.write(f"**{label}:** {hop.id} ({hop.lat:.4f}, {hop.lon:.4f})")

    # ── Batch tab ───────────────────────────────────────────
    with tab_batch:
        batch_tab(engine)

    # ── Nearest tab ─────────────────────────────────────────
    with tab_nearest:
        nearest_tab(engine)

    # ── SRTM Tiles tab (Enterprise) ─────────────────────────
    with tab_srtm:
        srtm_tab()

    # ── About tab ───────────────────────────────────────────
    with tab_about:
        st.subheader("TELECOM TOWER POWER")
        st.markdown(
            """
            **Professional telecom engineering platform** for cell tower coverage
            analysis, link budget calculations, and repeater chain planning.

            **Features:**
            - Point-to-point link analysis with FSPL, Fresnel clearance, and terrain-aware LOS
            - Multi-hop repeater chain optimization (Dijkstra bottleneck-shortest-path)
            - Real terrain elevation via SRTM tiles or Open-Elevation API
            - Interactive Folium map with tower/receiver visualization
            - Batch analysis with CSV upload/download
            - Nearest tower search

            **Frequency bands:** 700 MHz, 1800 MHz, 2600 MHz, 3500 MHz

            **Engine:** `telecom_tower_power.py` — standalone sync engine with no external
            API dependencies (terrain can fall back to Open-Elevation REST API when SRTM
            tiles are not available).

            ---
            Built for B2B telecom engineering workflows.
            """
        )


if __name__ == "__main__":
    main()
