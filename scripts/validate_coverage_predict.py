# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Compare /coverage/predict vs /analyze on a known obstructed link.

Link: São Bernardo do Campo (SP plateau, ~760 m AMSL) → Cubatão (~10 m AMSL).
The Serra do Mar ridge (~1100 m peak) sits between them, so a 30 m tower at
900 MHz cannot clear the Fresnel zone.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pprint import pprint

import coverage_predict as cp
from telecom_tower_power import LinkEngine
from telecom_tower_power_api import platform, Tower, Receiver, Band


TX_LAT, TX_LON = -23.6939, -46.5650   # São Bernardo do Campo
RX_LAT, RX_LON = -23.8950, -46.4250   # Cubatão
TX_H = 30.0
RX_H = 5.0
F_HZ = 700e6
TX_POWER = 43.0


import os
# Optional override via env vars (so we can probe other links without editing)
TX_LAT = float(os.getenv("TX_LAT", TX_LAT))
TX_LON = float(os.getenv("TX_LON", TX_LON))
RX_LAT = float(os.getenv("RX_LAT", RX_LAT))
RX_LON = float(os.getenv("RX_LON", RX_LON))
TX_H = float(os.getenv("TX_H", TX_H))


async def main() -> None:
    # --- Pull a real terrain profile via the platform's elevation service --
    profile = await platform.elevation.get_profile(TX_LAT, TX_LON, RX_LAT, RX_LON)
    if not profile:
        print("WARNING: no terrain profile returned (SRTM tiles missing?). "
              "Using flat-ground fallback.")
    else:
        print(f"Terrain profile: {len(profile)} samples, "
              f"min={min(profile):.0f} m, max={max(profile):.0f} m AMSL "
              f"(tx_ground={profile[0]:.0f}, rx_ground={profile[-1]:.0f})")

    d_km = LinkEngine.haversine_km(TX_LAT, TX_LON, RX_LAT, RX_LON)
    print(f"Distance: {d_km:.2f} km @ {F_HZ/1e6:.0f} MHz\n")

    # --- /analyze path (legacy physics) -------------------------------------
    tower = Tower(
        id="VALIDATION_TX",
        lat=TX_LAT, lon=TX_LON,
        height_m=TX_H,
        power_dbm=TX_POWER,
        bands=[Band.BAND_700],
        operator="TEST",
    )
    rx = Receiver(lat=RX_LAT, lon=RX_LON, height_m=RX_H, antenna_gain_dbi=12.0)
    legacy = await platform.analyze_link(tower, rx, terrain_profile=profile)
    print("=== /analyze (legacy physics) ===")
    print(f"  signal_dbm        = {legacy.signal_dbm:.2f}")
    print(f"  feasible          = {legacy.feasible}")
    print(f"  fresnel_clearance = {legacy.fresnel_clearance:.3f}")
    print(f"  los_ok            = {legacy.los_ok}")
    print(f"  recommendation    = {legacy.recommendation}")

    # --- /coverage/predict path (ML / physics-fallback) ---------------------
    pred = cp.predict_signal(
        d_km=d_km, f_hz=F_HZ,
        tx_h_m=TX_H, rx_h_m=RX_H,
        tx_power_dbm=TX_POWER,
        tx_gain_dbi=17.0, rx_gain_dbi=12.0,
        terrain_profile=profile,
        tx_ground_elev_m=profile[0] if profile else 0.0,
        rx_ground_elev_m=profile[-1] if profile else 0.0,
    )
    print("\n=== /coverage/predict ===")
    print(f"  signal_dbm     = {pred.signal_dbm}")
    print(f"  feasible       = {pred.feasible}")
    print(f"  confidence     = {pred.confidence}")
    print(f"  source         = {pred.source}")
    print(f"  model_version  = {pred.model_version}")

    # 1σ window from RMSE if a real model is loaded
    model = cp.get_model()
    if model is not None:
        sigma = model.rmse_db
        print(f"  ~68% CI        = [{pred.signal_dbm - sigma:.2f}, "
              f"{pred.signal_dbm + sigma:.2f}] dBm  (σ={sigma:.2f})")
        print(f"  ~95% CI        = [{pred.signal_dbm - 2*sigma:.2f}, "
              f"{pred.signal_dbm + 2*sigma:.2f}] dBm")
    else:
        print("  (no local model loaded → no RMSE-based CI available)")

    print("\n=== Δ ===")
    print(f"  predict - analyze = {pred.signal_dbm - legacy.signal_dbm:+.2f} dB")


if __name__ == "__main__":
    asyncio.run(main())
