# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
srtm_prefetch.py – Background SRTM tile pre-downloader for enterprise accounts.

When an enterprise customer signs up for coverage in a specific country,
this module queues a background job to populate the srtm_data/ volume
with all tiles for that country's bounding box.

Usage (standalone):
    python srtm_prefetch.py --country BR
    python srtm_prefetch.py --bounds -33.75,-73.99,5.27,-34.79

Usage (from API / webhook):
    from srtm_prefetch import prefetch_country, COUNTRY_BOUNDS
    threading.Thread(target=prefetch_country, args=("BR",), daemon=True).start()
"""

import argparse
import logging
import os
import threading
from typing import Dict, Optional, Tuple

from srtm_elevation import SRTMReader

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Country bounding boxes  (min_lat, min_lon, max_lat, max_lon)
#
# Values rounded outward to the nearest integer degree so that every
# SRTM tile touching the country is included.
# Source: Natural Earth / OSM boundary data.
# ------------------------------------------------------------------

COUNTRY_BOUNDS: Dict[str, Tuple[float, float, float, float]] = {
    # Latin America
    "BR": (-34.0, -74.0, 6.0, -35.0),   # Brazil  (note: lon wraps westward)
    "AR": (-56.0, -74.0, -22.0, -53.0),  # Argentina
    "CL": (-56.0, -76.0, -18.0, -67.0),  # Chile
    "CO": (-5.0, -80.0, 13.0, -67.0),    # Colombia
    "MX": (14.0, -118.0, 33.0, -87.0),   # Mexico
    "PE": (-19.0, -82.0, 1.0, -68.0),    # Peru
    # North America
    "US": (24.0, -125.0, 50.0, -67.0),   # Contiguous US
    "CA": (42.0, -141.0, 60.0, -52.0),   # Canada (SRTM limit 60°N)
    # Europe
    "DE": (47.0, 6.0, 55.0, 15.0),       # Germany
    "FR": (42.0, -5.0, 51.0, 9.0),       # France (metropolitan)
    "GB": (50.0, -8.0, 59.0, 2.0),       # United Kingdom
    "ES": (36.0, -10.0, 44.0, 4.0),      # Spain (peninsula)
    "IT": (36.0, 7.0, 47.0, 19.0),       # Italy
    "PT": (37.0, -10.0, 42.0, -6.0),     # Portugal
    "PL": (49.0, 14.0, 55.0, 25.0),      # Poland
    "NL": (51.0, 3.0, 54.0, 8.0),        # Netherlands
    "SE": (55.0, 11.0, 60.0, 24.0),      # Sweden (SRTM limit)
    "NO": (58.0, 5.0, 60.0, 31.0),       # Norway (SRTM limit)
    # Africa
    "ZA": (-35.0, 16.0, -22.0, 33.0),    # South Africa
    "NG": (4.0, 3.0, 14.0, 15.0),        # Nigeria
    "KE": (-5.0, 34.0, 5.0, 42.0),       # Kenya
    "EG": (22.0, 25.0, 32.0, 37.0),      # Egypt
    # Asia & Oceania
    "IN": (7.0, 68.0, 36.0, 98.0),       # India
    "ID": (-11.0, 95.0, 6.0, 141.0),     # Indonesia
    "JP": (24.0, 123.0, 46.0, 146.0),    # Japan
    "AU": (-44.0, 113.0, -10.0, 154.0),  # Australia
    "PH": (5.0, 117.0, 19.0, 127.0),     # Philippines
    "TH": (6.0, 98.0, 21.0, 106.0),      # Thailand
}

# Correct Brazil's bounding box  (min_lon < max_lon)
COUNTRY_BOUNDS["BR"] = (-34.0, -74.0, 6.0, -34.0)


# ------------------------------------------------------------------
# Prefetch helpers
# ------------------------------------------------------------------

_srtm: Optional[SRTMReader] = None


def _get_reader() -> SRTMReader:
    global _srtm
    if _srtm is None:
        _srtm = SRTMReader(os.getenv("SRTM_DATA_DIR", "./srtm_data"))
    return _srtm


def prefetch_country(
    country_code: str,
    *,
    srtm: Optional[SRTMReader] = None,
    on_progress=None,
) -> Tuple[int, int]:
    """
    Download all missing SRTM tiles for *country_code* (ISO 3166-1 alpha-2).

    Returns (downloaded_ok, total_missing).
    Raises KeyError if the country code is not in COUNTRY_BOUNDS.
    """
    code = country_code.upper()
    bounds = COUNTRY_BOUNDS.get(code)
    if bounds is None:
        raise KeyError(
            f"No bounding box defined for country '{code}'. "
            f"Available: {sorted(COUNTRY_BOUNDS)}"
        )
    reader = srtm or _get_reader()
    min_lat, min_lon, max_lat, max_lon = bounds
    logger.info("Starting SRTM prefetch for %s: bounds=%s", code, bounds)
    return reader.prefetch_bounds(
        min_lat, min_lon, max_lat, max_lon, on_progress=on_progress,
    )


def prefetch_bounds(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    *,
    srtm: Optional[SRTMReader] = None,
    on_progress=None,
) -> Tuple[int, int]:
    """Download all missing SRTM tiles for an arbitrary bounding box."""
    reader = srtm or _get_reader()
    return reader.prefetch_bounds(
        min_lat, min_lon, max_lat, max_lon, on_progress=on_progress,
    )


def prefetch_country_async(country_code: str) -> threading.Thread:
    """
    Launch a daemon thread to pre-download SRTM tiles for a country.

    Returns the Thread object (already started).
    """
    t = threading.Thread(
        target=prefetch_country,
        args=(country_code,),
        name=f"srtm-prefetch-{country_code}",
        daemon=True,
    )
    t.start()
    logger.info("Background SRTM prefetch started for %s", country_code)
    return t


def tile_status(country_code: str) -> Dict:
    """
    Return a summary of tile availability for a country.

    {
        "country": "BR",
        "total_tiles": 1600,
        "available": 1580,
        "missing": 20,
        "coverage_pct": 98.75,
    }
    """
    code = country_code.upper()
    bounds = COUNTRY_BOUNDS.get(code)
    if bounds is None:
        raise KeyError(f"No bounding box for '{code}'")
    reader = _get_reader()
    min_lat, min_lon, max_lat, max_lon = bounds
    all_tiles = reader.tiles_for_bounds(min_lat, min_lon, max_lat, max_lon)
    missing = reader.missing_tiles(min_lat, min_lon, max_lat, max_lon)
    total = len(all_tiles)
    avail = total - len(missing)
    return {
        "country": code,
        "total_tiles": total,
        "available": avail,
        "missing": len(missing),
        "coverage_pct": round(100 * avail / total, 2) if total else 100.0,
    }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pre-download SRTM tiles for a country or bounding box",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--country", "-c",
        help="ISO 3166-1 alpha-2 country code (e.g. BR, US, DE)",
    )
    group.add_argument(
        "--bounds", "-b",
        help="min_lat,min_lon,max_lat,max_lon (e.g. -34,-74,6,-34)",
    )
    parser.add_argument(
        "--status-only", action="store_true",
        help="Only report tile availability; do not download",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.country:
        code = args.country.upper()
        if args.status_only:
            info = tile_status(code)
            print(f"Country: {info['country']}")
            print(f"Total tiles: {info['total_tiles']}")
            print(f"Available:   {info['available']}")
            print(f"Missing:     {info['missing']}")
            print(f"Coverage:    {info['coverage_pct']}%")
            return

        def _progress(done, total):
            print(f"\r  [{done}/{total}]", end="", flush=True)

        ok, total = prefetch_country(code, on_progress=_progress)
        print(f"\nDone: {ok}/{total} tiles downloaded for {code}.")
    else:
        parts = [float(x) for x in args.bounds.split(",")]
        if len(parts) != 4:
            parser.error("--bounds requires exactly 4 comma-separated floats")
        min_lat, min_lon, max_lat, max_lon = parts

        def _progress(done, total):
            print(f"\r  [{done}/{total}]", end="", flush=True)

        ok, total = prefetch_bounds(
            min_lat, min_lon, max_lat, max_lon, on_progress=_progress,
        )
        print(f"\nDone: {ok}/{total} tiles downloaded.")


if __name__ == "__main__":
    main()
