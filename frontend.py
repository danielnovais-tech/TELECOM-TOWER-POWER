"""
frontend.py
TELECOM TOWER POWER — Streamlit SaaS frontend
Consumes the FastAPI backend for tower management, link analysis, and reporting.
"""

import os
import requests
import pandas as pd
import folium
import streamlit as st
from streamlit_folium import st_folium

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

DEMO_KEYS = {
    "Free (demo-key-free-001)": "demo-key-free-001",
    "Pro (demo-key-pro-001)": "demo-key-pro-001",
    "Enterprise (demo-key-enterprise-001)": "demo-key-enterprise-001",
}

st.set_page_config(
    page_title="Telecom Tower Power",
    page_icon="📡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — authentication & global settings
# ---------------------------------------------------------------------------

st.sidebar.title("📡 Telecom Tower Power")
st.sidebar.markdown("---")
st.sidebar.subheader("Authentication")

key_label = st.sidebar.selectbox("API Key", list(DEMO_KEYS.keys()))
api_key = st.sidebar.text_input(
    "Or paste a custom key",
    value=DEMO_KEYS[key_label],
    type="password",
)

HEADERS = {"X-API-Key": api_key}

st.sidebar.markdown("---")
st.sidebar.caption(f"Backend: `{API_BASE}`")


def api_get(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", headers=HEADERS, params=params, timeout=120)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
        return None
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to API. Is the backend running?")
        return None


def api_post(path, json_body=None, params=None):
    try:
        r = requests.post(
            f"{API_BASE}{path}", headers=HEADERS, json=json_body, params=params, timeout=120
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
        return None
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to API. Is the backend running?")
        return None


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_towers, tab_analyze, tab_repeater, tab_report = st.tabs(
    ["🗼 Towers", "📊 Link Analysis", "🔗 Repeater Planner", "📄 Reports"]
)

# =====================  TAB 1 — TOWERS  =====================
with tab_towers:
    st.header("Tower Management")

    col_add, col_list = st.columns([1, 2])

    with col_add:
        st.subheader("Add Tower")
        with st.form("add_tower"):
            tid = st.text_input("Tower ID", "TOWER_001")
            lat = st.number_input("Latitude", value=-15.7801, format="%.5f")
            lon = st.number_input("Longitude", value=-47.9292, format="%.5f")
            height = st.number_input("Height (m AGL)", value=45.0, min_value=1.0)
            operator = st.text_input("Operator", "Vivo")
            bands = st.multiselect(
                "Bands", ["700MHz", "1800MHz", "2600MHz", "3500MHz"],
                default=["700MHz", "1800MHz"],
            )
            power = st.number_input("TX Power (dBm)", value=46.0)
            submitted = st.form_submit_button("Add Tower")

        if submitted:
            body = {
                "id": tid,
                "lat": lat,
                "lon": lon,
                "height_m": height,
                "operator": operator,
                "bands": bands,
                "power_dbm": power,
            }
            result = api_post("/towers", json_body=body)
            if result:
                st.success(result["message"])

    with col_list:
        st.subheader("Registered Towers")
        if st.button("🔄 Refresh", key="refresh_towers"):
            pass  # triggers re-run

        data = api_get("/towers")
        if data and data.get("towers"):
            df = pd.DataFrame(data["towers"])
            st.dataframe(df, use_container_width=True)

            # Map
            avg_lat = df["lat"].mean()
            avg_lon = df["lon"].mean()
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=8)
            for _, row in df.iterrows():
                folium.Marker(
                    [row["lat"], row["lon"]],
                    popup=f"<b>{row['id']}</b><br>{row['operator']}<br>{row['height_m']}m",
                    icon=folium.Icon(color="red", icon="signal", prefix="fa"),
                ).add_to(m)
            st_folium(m, width=700, height=400)
        else:
            st.info("No towers registered yet. Add one on the left.")


# =====================  TAB 2 — LINK ANALYSIS  =====================
with tab_analyze:
    st.header("Point-to-Point Link Analysis")

    col_input, col_result = st.columns([1, 1])

    with col_input:
        towers_data = api_get("/towers")
        tower_ids = (
            [t["id"] for t in towers_data["towers"]]
            if towers_data and towers_data.get("towers")
            else []
        )
        if not tower_ids:
            st.warning("Add a tower first.")
            st.stop()

        tower_id = st.selectbox("Source Tower", tower_ids)
        st.markdown("**Receiver Location**")
        rx_lat = st.number_input("Receiver Latitude", value=-15.8500, format="%.5f", key="rx_lat")
        rx_lon = st.number_input("Receiver Longitude", value=-47.8100, format="%.5f", key="rx_lon")
        rx_height = st.number_input("Receiver Height (m)", value=12.0, key="rx_h")
        rx_gain = st.number_input("Antenna Gain (dBi)", value=15.0, key="rx_g")

        run_analysis = st.button("🔍 Run Analysis", type="primary")

    with col_result:
        if run_analysis:
            with st.spinner("Fetching terrain elevation & computing link budget…"):
                result = api_post(
                    "/analyze",
                    json_body={
                        "lat": rx_lat,
                        "lon": rx_lon,
                        "height_m": rx_height,
                        "antenna_gain_dbi": rx_gain,
                    },
                    params={"tower_id": tower_id},
                )
            if result:
                feasible = result["feasible"]
                st.subheader("Results")
                st.metric("Feasible", "✅ YES" if feasible else "❌ NO")

                c1, c2, c3 = st.columns(3)
                c1.metric("Distance", f"{result['distance_km']:.2f} km")
                c2.metric("RSSI", f"{result['signal_dbm']:.1f} dBm")
                c3.metric("Fresnel", f"{result['fresnel_clearance']:.3f}")

                if result["los_ok"]:
                    st.success("Line of Sight: Clear")
                else:
                    st.error("Line of Sight: Obstructed")

                st.info(f"**Recommendation:** {result['recommendation']}")

                # Map
                tower_info = next(
                    t for t in towers_data["towers"] if t["id"] == tower_id
                )
                m = folium.Map(
                    location=[
                        (tower_info["lat"] + rx_lat) / 2,
                        (tower_info["lon"] + rx_lon) / 2,
                    ],
                    zoom_start=11,
                )
                folium.Marker(
                    [tower_info["lat"], tower_info["lon"]],
                    popup=f"Tower: {tower_id}",
                    icon=folium.Icon(color="red", icon="signal", prefix="fa"),
                ).add_to(m)
                folium.Marker(
                    [rx_lat, rx_lon],
                    popup="Receiver",
                    icon=folium.Icon(color="blue", icon="home", prefix="fa"),
                ).add_to(m)
                line_color = "green" if feasible else "red"
                folium.PolyLine(
                    [[tower_info["lat"], tower_info["lon"]], [rx_lat, rx_lon]],
                    color=line_color,
                    weight=3,
                    dash_array="10",
                ).add_to(m)
                st_folium(m, width=600, height=350)


# =====================  TAB 3 — REPEATER PLANNER  =====================
with tab_repeater:
    st.header("Multi-Hop Repeater Planner")

    towers_data2 = api_get("/towers")
    tower_ids2 = (
        [t["id"] for t in towers_data2["towers"]]
        if towers_data2 and towers_data2.get("towers")
        else []
    )
    if not tower_ids2:
        st.warning("Add a tower first.")
        st.stop()

    col_rp_in, col_rp_out = st.columns([1, 1])

    with col_rp_in:
        rp_tower = st.selectbox("Source Tower", tower_ids2, key="rp_tower")
        st.markdown("**Target Location**")
        rp_lat = st.number_input("Target Latitude", value=-16.70, format="%.5f", key="rp_lat")
        rp_lon = st.number_input("Target Longitude", value=-49.25, format="%.5f", key="rp_lon")
        rp_h = st.number_input("Target Height (m)", value=12.0, key="rp_h")
        rp_g = st.number_input("Target Gain (dBi)", value=15.0, key="rp_g")
        max_hops = st.slider("Max Hops", 1, 5, 3)
        run_rp = st.button("🔗 Plan Repeaters", type="primary")

    with col_rp_out:
        if run_rp:
            with st.spinner("Optimizing repeater chain…"):
                chain = api_post(
                    "/plan_repeater",
                    json_body={
                        "lat": rp_lat,
                        "lon": rp_lon,
                        "height_m": rp_h,
                        "antenna_gain_dbi": rp_g,
                    },
                    params={"tower_id": rp_tower, "max_hops": max_hops},
                )
            if chain and chain.get("repeater_chain"):
                nodes = chain["repeater_chain"]
                st.subheader(f"Optimized Chain — {len(nodes)} node(s)")
                df_chain = pd.DataFrame(nodes)
                st.dataframe(df_chain[["id", "lat", "lon", "height_m"]], use_container_width=True)

                # Map with chain
                all_lats = [n["lat"] for n in nodes] + [rp_lat]
                all_lons = [n["lon"] for n in nodes] + [rp_lon]
                center = [sum(all_lats) / len(all_lats), sum(all_lons) / len(all_lons)]
                m = folium.Map(location=center, zoom_start=8)

                for i, node in enumerate(nodes):
                    color = "red" if i == 0 else "orange"
                    label = node["id"]
                    folium.Marker(
                        [node["lat"], node["lon"]],
                        popup=f"{label}<br>{node['height_m']}m",
                        icon=folium.Icon(color=color, icon="signal", prefix="fa"),
                    ).add_to(m)

                # Target
                folium.Marker(
                    [rp_lat, rp_lon],
                    popup="Target",
                    icon=folium.Icon(color="blue", icon="home", prefix="fa"),
                ).add_to(m)

                # Draw links
                coords = [[n["lat"], n["lon"]] for n in nodes] + [[rp_lat, rp_lon]]
                folium.PolyLine(coords, color="green", weight=3).add_to(m)
                st_folium(m, width=600, height=400)


# =====================  TAB 4 — REPORTS  =====================
with tab_report:
    st.header("Engineering Reports")

    towers_data3 = api_get("/towers")
    tower_ids3 = (
        [t["id"] for t in towers_data3["towers"]]
        if towers_data3 and towers_data3.get("towers")
        else []
    )
    if not tower_ids3:
        st.warning("Add a tower first.")
        st.stop()

    rpt_tower = st.selectbox("Tower", tower_ids3, key="rpt_tower")
    rpt_lat = st.number_input("Receiver Latitude", value=-15.85, format="%.5f", key="rpt_lat")
    rpt_lon = st.number_input("Receiver Longitude", value=-47.81, format="%.5f", key="rpt_lon")
    rpt_h = st.number_input("Receiver Height (m)", value=12.0, key="rpt_h")
    rpt_g = st.number_input("Antenna Gain (dBi)", value=15.0, key="rpt_g")

    col_json, col_pdf = st.columns(2)

    with col_json:
        if st.button("📋 JSON Report"):
            with st.spinner("Generating…"):
                report = api_get(
                    "/export_report",
                    params={
                        "tower_id": rpt_tower,
                        "lat": rpt_lat,
                        "lon": rpt_lon,
                        "height_m": rpt_h,
                        "antenna_gain": rpt_g,
                    },
                )
            if report:
                st.json(report)

    with col_pdf:
        if st.button("📄 Download PDF"):
            with st.spinner("Generating PDF…"):
                try:
                    r = requests.get(
                        f"{API_BASE}/export_report/pdf",
                        headers=HEADERS,
                        params={
                            "tower_id": rpt_tower,
                            "lat": rpt_lat,
                            "lon": rpt_lon,
                            "height_m": rpt_h,
                            "antenna_gain": rpt_g,
                        },
                        timeout=120,
                    )
                    r.raise_for_status()
                    st.download_button(
                        "⬇️ Save PDF",
                        data=r.content,
                        file_name=f"report_{rpt_tower}.pdf",
                        mime="application/pdf",
                    )
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 403:
                        st.warning("PDF export requires a Pro or Enterprise API key.")
                    else:
                        st.error(f"Error: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")
