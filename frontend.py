# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
frontend.py
TELECOM TOWER POWER — Streamlit SaaS frontend
Consumes the FastAPI backend via the typed api_client module.
"""

import os
import requests
import pandas as pd
import folium
import streamlit as st
from streamlit_folium import st_folium

from api_client import (
    TelecomTowerAPIClient,
    ReceiverInput,
    TowerInput,
    Band,
    RateLimitExceeded,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# Demo keys are read from DEMO_KEYS_UI env var (JSON: {"Label": "key", ...}).
# Falls back to legacy staging keys for local dev.
_demo_keys_raw = os.getenv("DEMO_KEYS_UI")
if _demo_keys_raw:
    import json as _json
    try:
        DEMO_KEYS = _json.loads(_demo_keys_raw)
    except Exception:
        DEMO_KEYS = {}
else:
    DEMO_KEYS = {
        "Free (demo_ttp_free_2604)": "demo_ttp_free_2604",
        "Starter (demo_ttp_starter_2604)": "demo_ttp_starter_2604",
        "Pro (demo_ttp_pro_2604)": "demo_ttp_pro_2604",
    }

PLAN_INFO = {
    "free": {"label": "Free", "price": "$0/mo", "features": "10 req/min · 20 towers · link analysis"},
    "pro": {"label": "Pro", "price": "$29/mo", "features": "100 req/min · 500 towers · PDF & batch export"},
    "enterprise": {"label": "Enterprise", "price": "$99/mo", "features": "1,000 req/min · 10k towers · priority support"},
}

st.set_page_config(
    page_title="Telecom Tower Power",
    page_icon="📡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------
if "user_api_key" not in st.session_state:
    st.session_state.user_api_key = ""
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "user_tier" not in st.session_state:
    st.session_state.user_tier = ""

# ---------------------------------------------------------------------------
# Sidebar — authentication & account management
# ---------------------------------------------------------------------------

st.sidebar.title("📡 Telecom Tower Power")
st.sidebar.markdown("---")
st.sidebar.subheader("Authentication")

auth_mode = st.sidebar.radio(
    "Key source",
    ["Demo keys", "My account", "Paste key"],
    horizontal=True,
)

if auth_mode == "Demo keys":
    key_label = st.sidebar.selectbox("API Key", list(DEMO_KEYS.keys()))
    api_key = DEMO_KEYS[key_label]
elif auth_mode == "Paste key":
    api_key = st.sidebar.text_input("API Key", type="password")
else:  # My account
    api_key = st.session_state.user_api_key or ""
    if api_key:
        st.sidebar.success(f"Signed in · **{st.session_state.user_tier.capitalize()}** plan")
        st.sidebar.code(api_key[:8] + "…", language=None)
    else:
        st.sidebar.info("Sign up or look up your key below.")

# Typed API client
api = TelecomTowerAPIClient(base_url=API_BASE, api_key=api_key)

# ---------------------------------------------------------------------------
# Sidebar — Sign Up / Billing
# ---------------------------------------------------------------------------
with st.sidebar.expander("🔑 Sign Up / Manage Account"):
    _signup_client = TelecomTowerAPIClient(base_url=API_BASE, api_key="")

    acct_tab_signup, acct_tab_lookup = st.tabs(["Sign Up", "Look Up Key"])

    with acct_tab_signup:
        signup_email = st.text_input("Email", key="signup_email", placeholder="you@company.com")
        signup_tier = st.selectbox(
            "Plan",
            list(PLAN_INFO.keys()),
            format_func=lambda t: f"{PLAN_INFO[t]['label']} — {PLAN_INFO[t]['price']}",
            key="signup_tier",
        )
        st.caption(PLAN_INFO[signup_tier]["features"])

        if st.button("Create Account", type="primary", key="btn_signup"):
            if not signup_email:
                st.warning("Enter your email.")
            else:
                try:
                    if signup_tier == "free":
                        result = _signup_client.signup_free(signup_email)
                        st.session_state.user_api_key = result["api_key"]
                        st.session_state.user_email = signup_email
                        st.session_state.user_tier = "free"
                        st.success("Account created!")
                        st.code(result["api_key"], language=None)
                        st.warning("Save this key — it won't be shown again.")
                        st.rerun()
                    else:
                        result = _signup_client.signup_checkout(signup_email, signup_tier)
                        checkout_url = result.get("checkout_url", "")
                        st.markdown(
                            f"[Complete payment on Stripe ↗]({checkout_url})",
                        )
                        st.info(
                            "After payment, return here and use **Look Up Key** "
                            "with your email to retrieve your API key."
                        )
                except requests.exceptions.HTTPError as e:
                    st.error(f"Error: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")

    with acct_tab_lookup:
        lookup_email = st.text_input("Email", key="lookup_email", placeholder="you@company.com")
        if st.button("Look Up", key="btn_lookup"):
            if not lookup_email:
                st.warning("Enter your email.")
            else:
                try:
                    info = _signup_client.signup_status(lookup_email)
                    st.session_state.user_api_key = info["api_key"]
                    st.session_state.user_email = info["email"]
                    st.session_state.user_tier = info.get("tier", "free")
                    st.success(f"**{info.get('tier', 'free').capitalize()}** plan")
                    st.code(info["api_key"], language=None)
                    st.rerun()
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404:
                        st.warning("No account found for this email.")
                    else:
                        st.error(f"Error: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")

        # Stripe return handling — session_id in query params
        query_params = st.query_params
        session_id = query_params.get("session_id")
        if session_id:
            try:
                result = _signup_client.signup_success(session_id)
                st.session_state.user_api_key = result["api_key"]
                st.session_state.user_email = result.get("email", "")
                st.session_state.user_tier = result.get("tier", "pro")
                st.success("Payment confirmed!")
                st.code(result["api_key"], language=None)
                st.warning("Save this key — it won't be shown again.")
            except requests.exceptions.HTTPError:
                st.info("Payment processing — use Look Up once it completes.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Backend: `{API_BASE}`")


def _show_rate_limit_sidebar():
    """Show rate-limit usage in the sidebar if data is available."""
    if api.rate_limit_remaining is not None and api.rate_limit_limit is not None:
        used = api.rate_limit_limit - api.rate_limit_remaining
        frac = used / max(api.rate_limit_limit, 1)
        st.sidebar.progress(frac)
        label = f"API usage: {used}/{api.rate_limit_limit} requests/min"
        if api.rate_limit_remaining <= 2:
            st.sidebar.warning(label)
        else:
            st.sidebar.caption(label)


def _handle_rate_limit_error():
    """Show a user-friendly rate-limit error with upgrade prompt."""
    limit = api.rate_limit_limit or "?"
    st.error(
        f"⚠️ **Rate limit exceeded** ({limit} requests/min for your plan). "
        "Wait a moment and try again, or upgrade to **Pro** for 100 req/min."
    )


def api_get(path, params=None):
    """Thin wrapper kept for backward-compat with tab code below."""
    try:
        r = api._session.get(f"{api.base_url}{path}", params=params, timeout=api.timeout)
        api._capture_rate_limit(r)
        r.raise_for_status()
        _show_rate_limit_sidebar()
        return r.json()
    except RateLimitExceeded:
        _handle_rate_limit_error()
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
        return None
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to API. Is the backend running?")
        return None


def api_post(path, json_body=None, params=None):
    """Thin wrapper kept for backward-compat with tab code below."""
    try:
        r = api._session.post(
            f"{api.base_url}{path}", json=json_body, params=params, timeout=api.timeout
        )
        api._capture_rate_limit(r)
        r.raise_for_status()
        _show_rate_limit_sidebar()
        return r.json()
    except RateLimitExceeded:
        _handle_rate_limit_error()
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
        return None
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to API. Is the backend running?")
        return None


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_towers, tab_analyze, tab_repeater, tab_report, tab_batch = st.tabs(
    ["🗼 Towers", "📊 Link Analysis", "🔗 Repeater Planner", "📄 Reports", "📦 Batch Jobs"]
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
            try:
                tower = TowerInput(
                    id=tid,
                    lat=lat,
                    lon=lon,
                    height_m=height,
                    operator=operator,
                    bands=[Band(b) for b in bands],
                    power_dbm=power,
                )
                result = api_post("/towers", json_body=tower.model_dump())
            except Exception as e:
                result = None
                st.error(f"Validation error: {e}")
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
                try:
                    rx = ReceiverInput(
                        lat=rx_lat,
                        lon=rx_lon,
                        height_m=rx_height,
                        antenna_gain_dbi=rx_gain,
                    )
                    result = api.analyze_link(tower_id, rx)
                    result = result.model_dump()
                    _show_rate_limit_sidebar()
                except RateLimitExceeded:
                    result = None
                    _handle_rate_limit_error()
                except requests.exceptions.HTTPError as e:
                    result = None
                    st.error(f"API error {e.response.status_code}: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    result = None
                    st.error("Cannot connect to API. Is the backend running?")
                except Exception as e:
                    result = None
                    st.error(str(e))
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
                try:
                    rx = ReceiverInput(
                        lat=rp_lat,
                        lon=rp_lon,
                        height_m=rp_h,
                        antenna_gain_dbi=rp_g,
                    )
                    chain = api.plan_repeater(rp_tower, rx, max_hops)
                    _show_rate_limit_sidebar()
                except RateLimitExceeded:
                    chain = None
                    _handle_rate_limit_error()
                except requests.exceptions.HTTPError as e:
                    chain = None
                    st.error(f"API error {e.response.status_code}: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    chain = None
                    st.error("Cannot connect to API. Is the backend running?")
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
                    pdf_bytes = api.export_report_pdf(
                        tower_id=rpt_tower,
                        lat=rpt_lat,
                        lon=rpt_lon,
                        height_m=rpt_h,
                        antenna_gain=rpt_g,
                    )
                    _show_rate_limit_sidebar()
                    st.download_button(
                        "⬇️ Save PDF",
                        data=pdf_bytes,
                        file_name=f"report_{rpt_tower}.pdf",
                        mime="application/pdf",
                    )
                except RateLimitExceeded:
                    _handle_rate_limit_error()
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 403:
                        st.warning("PDF export requires a Pro or Enterprise API key.")
                    else:
                        st.error(f"Error: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")


# =====================  TAB 5 — BATCH JOBS  =====================
with tab_batch:
    st.header("Batch Report Processing")

    towers_data4 = api_get("/towers")
    tower_ids4 = (
        [t["id"] for t in towers_data4["towers"]]
        if towers_data4 and towers_data4.get("towers")
        else []
    )
    if not tower_ids4:
        st.warning("Add a tower first.")
        st.stop()

    col_upload, col_status = st.columns([1, 1])

    with col_upload:
        st.subheader("Submit Batch")
        batch_tower = st.selectbox("Tower", tower_ids4, key="batch_tower")
        batch_rx_h = st.number_input(
            "Default Receiver Height (m)", value=10.0, key="batch_rx_h"
        )
        batch_gain = st.number_input(
            "Default Antenna Gain (dBi)", value=12.0, key="batch_gain"
        )
        uploaded_file = st.file_uploader(
            "Upload receiver CSV (columns: lat, lon, and optionally height, gain)",
            type="csv",
        )
        if uploaded_file and st.button("🚀 Submit Batch", type="primary"):
            with st.spinner("Uploading CSV and submitting batch…"):
                try:
                    r = api.batch_reports(
                        tower_id=batch_tower,
                        csv_file=uploaded_file,
                        filename=uploaded_file.name,
                        receiver_height_m=batch_rx_h,
                        antenna_gain_dbi=batch_gain,
                    )
                    _show_rate_limit_sidebar()
                    content_type = r.headers.get("content-type", "")

                    if "application/zip" in content_type:
                        st.success("Batch processed synchronously!")
                        st.download_button(
                            "⬇️ Download ZIP",
                            data=r.content,
                            file_name=f"batch_reports_{batch_tower}.zip",
                            mime="application/zip",
                            key="batch_sync_dl",
                        )
                    else:
                        result = r.json()
                        st.session_state.batch_job_id = result["job_id"]
                        st.info(
                            f"Job queued: **{result['total']}** receivers. "
                            f"Job ID: `{result['job_id']}`"
                        )
                except RateLimitExceeded:
                    _handle_rate_limit_error()
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 403:
                        st.warning("Batch reports require a Pro or Enterprise API key.")
                    else:
                        st.error(f"API error {e.response.status_code}: {e.response.text}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")

    with col_status:
        st.subheader("Job Status")
        job_id_input = st.text_input(
            "Job ID",
            value=st.session_state.get("batch_job_id", ""),
            key="batch_job_input",
        )

        if job_id_input and st.button("🔄 Check Status"):
            with st.spinner("Polling job status…"):
                status = api_get(f"/jobs/{job_id_input}")
            if status:
                st.session_state.batch_job_status = status

        job_status = st.session_state.get("batch_job_status")
        if job_status:
            job_st = job_status["status"]
            progress = job_status.get("progress", 0)
            total = job_status.get("total", 1)

            if job_st == "completed":
                st.success(f"Job completed — {total} reports generated.")
                st.progress(1.0)
            elif job_st == "failed":
                st.error(f"Job failed: {job_status.get('error', 'Unknown error')}")
                st.progress(progress / max(total, 1))
            elif job_st == "running":
                st.info(f"Running… {progress}/{total} receivers processed.")
                st.progress(progress / max(total, 1))
            else:
                st.info(f"Status: **{job_st}** — {progress}/{total}")
                st.progress(progress / max(total, 1))

            if job_st == "completed":
                dl_url = job_status.get("download_url", f"/jobs/{job_status['job_id']}/download")
                try:
                    dl_bytes = api.job_download(job_status["job_id"])
                    st.download_button(
                        "⬇️ Download ZIP",
                        data=dl_bytes,
                        file_name=f"batch_reports_{job_status.get('tower_id', 'job')}.zip",
                        mime="application/zip",
                        key="batch_async_dl",
                    )
                except requests.exceptions.HTTPError as e:
                    st.error(f"Download error: {e.response.status_code}")
                except requests.exceptions.ConnectionError:
                    st.error("Cannot connect to API.")
