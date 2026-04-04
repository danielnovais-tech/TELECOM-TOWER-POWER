"""
telecom_tower_power.py
Professional telecom engineering platform for cell tower coverage analysis.
Focus: rural areas, fixed wireless access (FWA), repeater tower planning.
Author: Based on the TELECOM TOWER POWER narrative (formerly Mapa das Torres).
"""

import math
import json
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from enum import Enum

# ------------------------------------------------------------
# Data models
# ------------------------------------------------------------

class Band(Enum):
    BAND_700 = 700e6   # 700 MHz - good range
    BAND_1800 = 1.8e9  # 1800 MHz
    BAND_2600 = 2.6e9  # 2600 MHz
    BAND_3500 = 3.5e9  # 3.5 GHz - 5G

@dataclass
class Tower:
    id: str
    lat: float
    lon: float
    height_m: float          # antenna height above ground
    operator: str            # Vivo, Claro, TIM, etc.
    bands: List[Band]
    power_dbm: float = 43.0  # typical eirp per band (dBm)
    frequency_mhz: float = None  # auto-set from primary band

    def __post_init__(self):
        if self.frequency_mhz is None and self.bands:
            self.frequency_mhz = self.bands[0].value / 1e6

@dataclass
class Receiver:
    lat: float
    lon: float
    height_m: float = 10.0    # typical antenna mast height for rural
    antenna_gain_dbi: float = 12.0

@dataclass
class LinkResult:
    feasible: bool
    signal_dbm: float         # received signal strength
    fresnel_clearance: float  # fraction of first Fresnel zone cleared
    los_ok: bool
    distance_km: float
    recommendation: str

# ------------------------------------------------------------
# Propagation & Link Budget Engine
# ------------------------------------------------------------

class LinkEngine:
    """Calculate point-to-point link budget using ITU-R P.526 (LOS) and free-space + terrain."""

    @staticmethod
    def haversine_km(lat1, lon1, lat2, lon2):
        R = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    @staticmethod
    def free_space_path_loss(d_km, f_hz):
        """FSPL in dB."""
        d_m = d_km * 1000
        return 20 * math.log10(d_m) + 20 * math.log10(f_hz) - 147.55

    @staticmethod
    def fresnel_radius(d_km, f_hz, d1_km, d2_km):
        """First Fresnel zone radius (meters) at a point along the path."""
        d = d_km * 1000
        d1 = d1_km * 1000
        d2 = d2_km * 1000
        return math.sqrt((f_hz * d1 * d2) / ((d1 + d2) * 299792458))  # simplified

    @staticmethod
    def terrain_clearance(terrain_profile: List[float], d_km: float, f_hz: float, tx_h: float, rx_h: float) -> float:
        """
        Simplified terrain clearance check.
        Returns minimum fraction of first Fresnel zone clearance (0 to 1+).
        Assumes terrain_profile is list of heights (m) at equally spaced points.
        """
        n = len(terrain_profile)
        if n < 2:
            return 1.0
        step = d_km / (n - 1)
        min_clearance = float('inf')
        for i, ground_h in enumerate(terrain_profile):
            d_i = i * step
            # straight line height between tx and rx at distance d_i
            line_h = tx_h + (rx_h - tx_h) * (d_i / d_km)
            clearance = line_h - ground_h
            # compute Fresnel radius at this point
            d1 = d_i
            d2 = d_km - d_i
            if d1 <= 0 or d2 <= 0:
                continue
            fresnel_r = LinkEngine.fresnel_radius(d_km, f_hz, d1, d2)
            if fresnel_r > 0:
                min_clearance = min(min_clearance, clearance / fresnel_r)
        return min_clearance if min_clearance != float('inf') else 1.0

    @staticmethod
    def estimate_signal(tx_power_dbm: float, tx_gain_dbi: float, rx_gain_dbi: float,
                        f_hz: float, d_km: float, extra_loss_db: float = 0.0) -> float:
        """Received signal power in dBm."""
        fspl = LinkEngine.free_space_path_loss(d_km, f_hz)
        return tx_power_dbm + tx_gain_dbi + rx_gain_dbi - fspl - extra_loss_db

# ------------------------------------------------------------
# Main Platform Class
# ------------------------------------------------------------

class TelecomTowerPower:
    """Main SaaS platform for cell tower coverage analysis and repeater planning."""

    def __init__(self):
        self.towers: Dict[str, Tower] = {}
        self.dtm_cache = {}   # simple terrain cache (lat,lng) -> elevation

    def add_tower(self, tower: Tower):
        self.towers[tower.id] = tower

    def load_towers_from_geojson(self, filepath: str):
        """Load towers from a GeoJSON file (simulated)."""
        # In real implementation, read GeoJSON with lat/lon and properties.
        pass

    def find_nearest_towers(self, lat: float, lon: float, operator: Optional[str] = None, limit: int = 5) -> List[Tower]:
        """Return nearest towers sorted by distance."""
        distances = []
        for tower in self.towers.values():
            if operator and tower.operator != operator:
                continue
            d = LinkEngine.haversine_km(lat, lon, tower.lat, tower.lon)
            distances.append((d, tower))
        distances.sort(key=lambda x: x[0])
        return [t for _, t in distances[:limit]]

    def analyze_link(self, tower: Tower, receiver: Receiver,
                     terrain_profile: Optional[List[float]] = None) -> LinkResult:
        """
        Full point-to-point analysis including LOS, Fresnel clearance, and RSSI.
        terrain_profile: list of ground heights (m) at equally spaced points along the path.
        """
        d_km = LinkEngine.haversine_km(tower.lat, tower.lon, receiver.lat, receiver.lon)
        f_hz = tower.bands[0].value  # use primary band

        # Estimate received signal (free-space base)
        # Assume tower antenna gain ~17 dBi typical for sectorial, receiver gain given
        tx_gain = 17.0
        rx_gain = receiver.antenna_gain_dbi
        rssi = LinkEngine.estimate_signal(tower.power_dbm, tx_gain, rx_gain, f_hz, d_km)

        # Terrain & LOS check
        los_ok = True
        fresnel_clear = 1.0
        if terrain_profile:
            fresnel_clear = LinkEngine.terrain_clearance(
                terrain_profile, d_km, f_hz, tower.height_m, receiver.height_m
            )
            los_ok = fresnel_clear > 0.3   # rule of thumb: 60% clearance needed for reliable link
            if fresnel_clear < 0.6:
                rssi -= (0.6 - fresnel_clear) * 10  # empirical extra loss due to obstruction

        feasible = los_ok and (rssi > -95)   # -95 dBm threshold for 4G/5G reliable

        # Recommendation for repeater tower planning
        if not los_ok:
            recommendation = (
                f"Insufficient Fresnel clearance ({fresnel_clear:.2f}). "
                f"Increase receiver height to > {tower.height_m + 10:.0f}m or move tower."
            )
        elif rssi < -95:
            recommendation = (
                f"Signal too low ({rssi:.1f} dBm). "
                f"Consider higher gain antenna or use a repeater tower at distance {d_km/2:.1f}km."
            )
        else:
            recommendation = f"Good link. RSSI = {rssi:.1f} dBm. Clear LOS."

        return LinkResult(
            feasible=feasible,
            signal_dbm=rssi,
            fresnel_clearance=fresnel_clear,
            los_ok=los_ok,
            distance_km=d_km,
            recommendation=recommendation
        )

    def plan_repeater_chain(self, start_tower: Tower, target_receiver: Receiver,
                            max_hops: int = 3, min_rssi_db: float = -85) -> List[Tower]:
        """
        Suggest intermediate repeater tower positions (simplified greedy algorithm).
        Returns list of tower positions (including start_tower) that can reach target.
        """
        # This is a high-level planning function: given no existing towers, propose new sites.
        # For MVP, just return start_tower and a mock intermediate.
        result = [start_tower]
        # logic: find point halfway where LOS is feasible and signal > threshold
        # In real implementation, would iterate over terrain and propose lat/lon.
        # Here we simulate:
        mid_lat = (start_tower.lat + target_receiver.lat) / 2
        mid_lon = (start_tower.lon + target_receiver.lon) / 2
        intermediate = Tower(
            id="proposed_repeater_1",
            lat=mid_lat, lon=mid_lon, height_m=40.0,
            operator=start_tower.operator, bands=start_tower.bands, power_dbm=43.0
        )
        result.append(intermediate)
        return result

    def export_report(self, link_result: LinkResult, tower: Tower, receiver: Receiver) -> str:
        """Generate a professional PDF-ready report (JSON summary here)."""
        report = {
            "platform": "TELECOM TOWER POWER",
            "tower_id": tower.id,
            "tower_location": {"lat": tower.lat, "lon": tower.lon},
            "receiver_location": {"lat": receiver.lat, "lon": receiver.lon},
            "distance_km": round(link_result.distance_km, 2),
            "signal_dbm": round(link_result.signal_dbm, 1),
            "los_ok": link_result.los_ok,
            "fresnel_clearance_pct": round(link_result.fresnel_clearance * 100, 1),
            "feasible": link_result.feasible,
            "recommendation": link_result.recommendation
        }
        return json.dumps(report, indent=2)

# ------------------------------------------------------------
# Example usage (simulating a rural repeater project)
# ------------------------------------------------------------

if __name__ == "__main__":
    platform = TelecomTowerPower()

    # Add a real tower (e.g., in rural Brazil)
    tower_agro = Tower(
        id="VIVO_AGRO_001",
        lat=-15.7801, lon=-47.9292,  # near Brasília
        height_m=45.0,
        operator="Vivo",
        bands=[Band.BAND_700, Band.BAND_1800],
        power_dbm=46.0
    )
    platform.add_tower(tower_agro)

    # Receiver at a farm 12 km away
    farm_receiver = Receiver(
        lat=-15.8500, lon=-47.8100,
        height_m=12.0,
        antenna_gain_dbi=15.0
    )

    # Simulate terrain profile (heights in meters at 20 points along path)
    # In reality, fetch from SRTM/GMTED. Here we create a mock profile with a hill.
    terrain_profile = [850.0, 855.0, 860.0, 870.0, 880.0, 890.0, 900.0, 910.0, 905.0, 900.0,
                       890.0, 880.0, 870.0, 860.0, 855.0, 850.0, 848.0, 845.0, 843.0, 840.0]

    # Analyze link
    result = platform.analyze_link(tower_agro, farm_receiver, terrain_profile)

    print("=== TELECOM TOWER POWER - Link Analysis ===")
    print(f"Distance: {result.distance_km:.2f} km")
    print(f"LOS OK: {result.los_ok}")
    print(f"Fresnel clearance: {result.fresnel_clearance*100:.1f}%")
    print(f"Estimated RSSI: {result.signal_dbm:.1f} dBm")
    print(f"Feasible: {result.feasible}")
    print(f"Recommendation: {result.recommendation}")

    # Generate report
    report_json = platform.export_report(result, tower_agro, farm_receiver)
    print("\n--- Engineering Report (JSON) ---")
    print(report_json)

    # Repeater planning suggestion
    print("\n--- Repeater Chain Proposal ---")
    chain = platform.plan_repeater_chain(tower_agro, farm_receiver)
    print(f"Suggested repeater tower at: ({chain[1].lat:.4f}, {chain[1].lon:.4f}), height {chain[1].height_m}m")
