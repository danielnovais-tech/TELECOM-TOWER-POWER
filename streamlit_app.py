"""
streamlit_app.py
Streamlit Community Cloud frontend for TELECOM TOWER POWER.

Uses the standalone sync engine (telecom_tower_power.py) directly —
no FastAPI backend required.
"""

import csv
import io
import math
import os

import folium
import pandas as pd
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


# ── Batch analysis ──────────────────────────────────────────
def batch_tab(engine: TelecomTowerPower):
    st.subheader("Batch link analysis")
    st.markdown("Upload a CSV with columns: `lat`, `lon`, `height`, `gain`")

    tower_ids = sorted(engine.towers.keys())
    if not tower_ids:
        st.warning("No towers loaded.")
        return

    col1, col2 = st.columns([1, 2])
    with col1:
        batch_tower_id = st.selectbox("Tower for batch", tower_ids, key="batch_tower")
        batch_file = st.file_uploader("Receivers CSV", type=["csv"], key="batch_csv")

    if batch_file is not None and st.button("Run batch analysis", key="run_batch"):
        tower = engine.towers[batch_tower_id]
        text = batch_file.getvalue().decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        results = []
        progress = st.progress(0, text="Analyzing...")
        rows = list(reader)
        total = len(rows)

        for i, row in enumerate(rows):
            rx = Receiver(
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                height_m=float(row.get("height", 10)),
                antenna_gain_dbi=float(row.get("gain", 12)),
            )
            lr = engine.analyze_link(tower, rx)
            results.append({
                "Receiver": f"({rx.lat:.4f}, {rx.lon:.4f})",
                "Distance (km)": round(lr.distance_km, 2),
                "Signal (dBm)": round(lr.signal_dbm, 1),
                "Fresnel": round(lr.fresnel_clearance, 2),
                "LOS": "✅" if lr.los_ok else "❌",
                "Feasible": "✅" if lr.feasible else "❌",
                "Recommendation": lr.recommendation,
            })
            progress.progress((i + 1) / total, text=f"Analyzed {i + 1}/{total}")

        progress.empty()
        df = pd.DataFrame(results)
        with col2:
            feasible_count = sum(1 for r in results if r["Feasible"] == "✅")
            m1, m2, m3 = st.columns(3)
            m1.metric("Total", total)
            m2.metric("Feasible", feasible_count)
            m3.metric("Failed", total - feasible_count)
        st.dataframe(df, use_container_width=True)

        csv_out = df.to_csv(index=False)
        st.download_button(
            "📥 Download results CSV",
            csv_out,
            file_name="batch_results.csv",
            mime="text/csv",
        )


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


# ── Main ────────────────────────────────────────────────────
def main():
    engine = get_engine()
    ensure_towers_loaded(engine)

    tab_map, tab_batch, tab_nearest, tab_about = st.tabs(
        ["🗺️ Map & Analysis", "📊 Batch Analysis", "📍 Nearest Towers", "ℹ️ About"]
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
