"""
geocoder_br.py – Geocode Brazilian city/state pairs to lat/lon.

Uses a pre-geocoded lookup table of ~5,570 Brazilian municipalities
(from IBGE via kelvins/Municipios-Brasileiros) as the primary source.
Falls back to Nominatim (OpenStreetMap) only for cache misses.

Usage:
    from geocoder_br import BrazilGeocoder
    geo = BrazilGeocoder()
    lat, lon = geo.geocode("São Paulo", "SP")
"""

import csv
import json
import os
import time
import unicodedata
from typing import Dict, Optional, Tuple

import requests

CACHE_PATH = os.getenv("GEOCODE_CACHE_PATH", "geocode_cache_br.json")
MUNICIPIOS_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "municipios_brasileiros.csv"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "telecom-tower-power/1.0 (tower data loader)"
MIN_REQUEST_INTERVAL = 1.1  # seconds – Nominatim policy

# IBGE codigo_uf → 2-letter state abbreviation
_UF_CODE_TO_ABBR = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
    21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
    28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP",
    41: "PR", 42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF",
}


def _normalize(text: str) -> str:
    """Strip accents and lowercase for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


class BrazilGeocoder:
    """Geocode Brazilian municipalities with a pre-built lookup table + cache."""

    def __init__(self, cache_path: str = CACHE_PATH):
        self.cache_path = cache_path
        self._cache: Dict[str, Tuple[float, float]] = {}
        self._lookup: Dict[str, Tuple[float, float]] = {}
        self._last_request = 0.0
        self._load_lookup()
        self._load_cache()

    # ── pre-geocoded lookup table ────────────────────────────────

    def _load_lookup(self) -> None:
        """Load the pre-geocoded municipalities CSV (IBGE coordinates)."""
        if not os.path.exists(MUNICIPIOS_CSV):
            print(f"  [geocoder] lookup table not found: {MUNICIPIOS_CSV}")
            return
        count = 0
        with open(MUNICIPIOS_CSV, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                nome = row.get("nome", "").strip()
                lat_s = row.get("latitude", "")
                lon_s = row.get("longitude", "")
                uf_code = row.get("codigo_uf", "")
                if not (nome and lat_s and lon_s and uf_code):
                    continue
                uf = _UF_CODE_TO_ABBR.get(int(uf_code), "")
                if not uf:
                    continue
                # Store under both exact and normalized keys
                key_exact = f"{nome.lower()}|{uf}"
                key_norm = f"{_normalize(nome)}|{uf}"
                coords = (float(lat_s), float(lon_s))
                self._lookup[key_exact] = coords
                if key_norm != key_exact:
                    self._lookup[key_norm] = coords
                count += 1
        print(f"  [geocoder] loaded {count} municipalities from lookup table")

    # ── cache ────────────────────────────────────────────────────

    def _load_cache(self) -> None:
        if os.path.exists(self.cache_path):
            with open(self.cache_path, encoding="utf-8") as f:
                raw = json.load(f)
            self._cache = {k: tuple(v) for k, v in raw.items()}

    def _save_cache(self) -> None:
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=1)

    def _cache_key(self, city: str, state: str) -> str:
        return f"{city.strip().lower()}|{state.strip().upper()}"

    # ── public API ───────────────────────────────────────────────

    def geocode(self, city: str, state: str) -> Optional[Tuple[float, float]]:
        """Return (lat, lon) for a Brazilian city/state pair, or None.

        Resolution order: lookup table → JSON cache → Nominatim.
        """
        key = self._cache_key(city, state)

        # 1. Pre-geocoded lookup (instant)
        if key in self._lookup:
            return self._lookup[key]
        # Try normalized (accent-stripped) match
        key_norm = f"{_normalize(city)}|{state.strip().upper()}"
        if key_norm in self._lookup:
            return self._lookup[key_norm]

        # 2. Persistent JSON cache
        if key in self._cache:
            return self._cache[key]

        # 3. Nominatim fallback (slow, rate-limited)
        coords = self._query_nominatim(city, state)
        if coords:
            self._cache[key] = coords
            self._save_cache()
        return coords

    def geocode_batch(
        self, pairs: list[Tuple[str, str]]
    ) -> Dict[str, Optional[Tuple[float, float]]]:
        """Geocode a list of (city, state) pairs. Returns dict keyed by 'city|STATE'."""
        results: Dict[str, Optional[Tuple[float, float]]] = {}
        unique = {self._cache_key(c, s): (c, s) for c, s in pairs}
        total = len(unique)
        done = 0
        nominatim_calls = 0
        for key, (city, state) in unique.items():
            coords = self.geocode(city, state)
            results[key] = coords
            done += 1
            if key not in self._lookup and f"{_normalize(city)}|{state.strip().upper()}" not in self._lookup:
                nominatim_calls += 1
            if done % 200 == 0:
                print(f"  geocoded {done}/{total} municipalities")
        if nominatim_calls:
            print(f"  {nominatim_calls} municipalities required Nominatim fallback")
        return results

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def lookup_size(self) -> int:
        return len(self._lookup)

    # ── Nominatim query ──────────────────────────────────────────

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request = time.monotonic()

    def _query_nominatim(
        self, city: str, state: str
    ) -> Optional[Tuple[float, float]]:
        self._throttle()
        params = {
            "city": city,
            "state": state,
            "country": "Brazil",
            "format": "json",
            "limit": 1,
        }
        headers = {"User-Agent": USER_AGENT}
        try:
            resp = requests.get(
                NOMINATIM_URL, params=params, headers=headers, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            print(f"  [geocode] failed for {city}, {state}: {exc}")
        return None
