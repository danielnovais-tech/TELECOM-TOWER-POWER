"""
load_anatel.py – Import Brazilian cell tower data from ANATEL (Agência Nacional
de Telecomunicações) into the platform.

ANATEL data is obtained via Brazil's Freedom of Information Act (LAI) and
published as Excel spreadsheets.  The data lists every licensed base station
(ERB - Estação Rádio Base) with operator, equipment, city, and state — but
**no geographic coordinates**.  This script geocodes each municipality via
Nominatim and loads the results.

Imported dataset stats (ERBs_com_equipamentos_v2.xlsx):
    - 443,396 raw records → 105,240 unique stations after deduplication
    - 12 operators: Claro, Vivo, TIM, Oi, Algar, Brisanet, Unifique,
      Sercomtel, and others
    - 5,570 municipalities geocoded across all 27 Brazilian states
    - Default parameters: 35 m height, 43 dBm power, 700/1800 MHz bands

Data source:
    https://github.com/LuSrodri/ERBs_per_city_per_operators_brazil
    File: ERBs_com_equipamentos_v2.xlsx

ANATEL columns:
    NumCnpjCpf, Prestadora, NumEstacao, CodEquipamentoTransmissor,
    Fabricante Agrupado, Fabricante, CN, Município, UF

Usage:
    python load_anatel.py --file ERBs_com_equipamentos_v2.xlsx
    python load_anatel.py --file anatel_data.xlsx --dry-run
    python load_anatel.py --file anatel_data.csv   # also accepts CSV
"""

import argparse
import csv
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

from geocoder_br import BrazilGeocoder
from tower_db import TowerStore

try:
    import openpyxl
except ImportError:
    openpyxl = None

# ── Operator name normalisation ──────────────────────────────────
_OPERATOR_MAP = {
    "CLARO": "Claro",
    "CLARO S.A": "Claro",
    "CLARO S.A.": "Claro",
    "CLARO S/A": "Claro",
    "AMERICEL": "Claro",
    "BCP": "Claro",
    "TIM": "TIM",
    "TIM S.A": "TIM",
    "TIM S.A.": "TIM",
    "TIM S/A": "TIM",
    "TIM CELULAR": "TIM",
    "TIM CELULAR S.A.": "TIM",
    "INTELIG": "TIM",
    "VIVO": "Vivo",
    "VIVO S.A": "Vivo",
    "VIVO S.A.": "Vivo",
    "VIVO S/A": "Vivo",
    "TELEFONICA": "Vivo",
    "TELEFÔNICA": "Vivo",
    "TELEFONICA BRASIL": "Vivo",
    "TELEFÔNICA BRASIL": "Vivo",
    "TELEFONICA BRASIL S.A.": "Vivo",
    "TELEFÔNICA BRASIL S.A.": "Vivo",
    "GVT": "Vivo",
    "OI": "Oi",
    "OI MOVEL": "Oi",
    "OI MÓVEL": "Oi",
    "OI MOVEL S.A": "Oi",
    "OI MÓVEL S.A.": "Oi",
    "OI S.A": "Oi",
    "OI S.A.": "Oi",
    "OI S/A": "Oi",
    "TELEMAR": "Oi",
    "TNL PCS": "Oi",
    "BRASIL TELECOM": "Oi",
    "BRT": "Oi",
    "ALGAR": "Algar",
    "ALGAR TELECOM": "Algar",
    "ALGAR TELECOM S/A": "Algar",
    "ALGAR TELECOM S.A.": "Algar",
    "CTBC": "Algar",
    "CTBC TELECOM": "Algar",
    "SERCOMTEL": "Sercomtel",
    "NEXTEL": "Nextel",
    "NEXTEL TELECOMUNICACOES": "Nextel",
    "NEXTEL TELECOMUNICAÇÕES": "Nextel",
}

# Default tower parameters for ANATEL data (no radio-specific info)
_DEFAULT_HEIGHT = 35.0
_DEFAULT_POWER = 43.0
_DEFAULT_BANDS = ["700MHz", "1800MHz"]

# Small random offset (degrees) to spread towers within a city
_CITY_JITTER = 0.008  # ~800m


def _normalise_operator(raw: str) -> str:
    """Map raw ANATEL operator name to a platform-standard name."""
    key = raw.strip().upper()
    if key in _OPERATOR_MAP:
        return _OPERATOR_MAP[key]
    # Fuzzy fallback: check if any known name is contained
    for pattern, name in _OPERATOR_MAP.items():
        if pattern in key:
            return name
    return raw.strip()


def _read_xlsx(path: str) -> List[Dict[str, str]]:
    """Read an ANATEL XLSX file into a list of row dicts."""
    if openpyxl is None:
        print("ERROR: openpyxl is required to read .xlsx files", file=sys.stderr)
        print("  pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows[0])]
    return [
        {h: (str(v).strip() if v is not None else "") for h, v in zip(headers, row)}
        for row in rows[1:]
    ]


def _read_csv(path: str) -> List[Dict[str, str]]:
    """Read an ANATEL CSV file (fallback format)."""
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_anatel_file(path: str) -> List[Dict[str, str]]:
    """Read ANATEL data from either XLSX or CSV."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return _read_xlsx(path)
    return _read_csv(path)


def transform_anatel_records(
    records: List[Dict[str, str]],
    geocoder: BrazilGeocoder,
) -> List[Dict[str, Any]]:
    """Transform raw ANATEL rows into platform tower dicts.

    Deduplicates by station number (NumEstacao) and geocodes
    each unique city/state pair.
    """
    # Collect unique city/state pairs for batch geocoding
    city_state_pairs: set[Tuple[str, str]] = set()
    for rec in records:
        city = rec.get("Município", rec.get("Municipio", "")).strip()
        state = rec.get("UF", "").strip()
        if city and state:
            city_state_pairs.add((city, state))

    print(f"  {len(city_state_pairs)} unique municipalities to geocode")
    geo_results = geocoder.geocode_batch(list(city_state_pairs))

    # Deduplicate by station number
    stations: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        station_id = rec.get("NumEstacao", "").strip()
        if not station_id:
            continue
        if station_id in stations:
            continue

        city = rec.get("Município", rec.get("Municipio", "")).strip()
        state = rec.get("UF", "").strip()
        operator_raw = rec.get("Prestadora", "").strip()

        cache_key = f"{city.lower()}|{state.upper()}"
        coords = geo_results.get(cache_key)
        if not coords:
            continue

        lat, lon = coords
        # Add small jitter so towers in the same city don't stack
        lat += random.uniform(-_CITY_JITTER, _CITY_JITTER)
        lon += random.uniform(-_CITY_JITTER, _CITY_JITTER)

        operator = _normalise_operator(operator_raw)

        stations[station_id] = {
            "id": f"ANATEL_{station_id}",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "height_m": _DEFAULT_HEIGHT,
            "operator": operator,
            "bands": _DEFAULT_BANDS,
            "power_dbm": _DEFAULT_POWER,
        }

    print(f"  {len(stations)} unique stations after deduplication")
    return list(stations.values())


def load_anatel(
    file_path: str,
    *,
    batch_size: int = 5000,
    dry_run: bool = False,
) -> int:
    """Main entry point: read, geocode, transform, and load ANATEL data.

    Returns the total number of towers loaded.
    """
    if not os.path.exists(file_path):
        print(f"ERROR: file not found: {file_path}", file=sys.stderr)
        return 0

    print(f"Reading ANATEL data from {file_path} ...")
    records = read_anatel_file(file_path)
    print(f"  {len(records)} raw records")

    if not records:
        print("No records found.")
        return 0

    geocoder = BrazilGeocoder()
    print(f"  geocode cache: {geocoder.cache_size} entries")

    towers = transform_anatel_records(records, geocoder)

    if not towers:
        print("No towers after transformation.")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] Would load {len(towers)} ANATEL towers. Sample:")
        for t in towers[:5]:
            print(f"  {t['id']:30s}  {t['operator']:10s}  "
                  f"({t['lat']:.4f}, {t['lon']:.4f})")
        return len(towers)

    store = TowerStore()
    print(f"\nLoading {len(towers)} towers into {store.backend} database...")

    loaded = 0
    for i in range(0, len(towers), batch_size):
        batch = towers[i : i + batch_size]
        written = store.upsert_many(batch)
        loaded += written
        print(f"  batch {i // batch_size + 1}: "
              f"{written} towers (total {loaded}/{len(towers)})")

    print(f"\nDone. {loaded} ANATEL towers loaded. "
          f"Total towers in DB: {store.count()}")
    return loaded


def main():
    parser = argparse.ArgumentParser(
        description="Load Brazilian cell towers from ANATEL data into the database"
    )
    parser.add_argument(
        "--file", dest="file_path", required=True,
        help="Path to ANATEL XLSX or CSV file",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000,
        help="DB insert batch size (default: 5000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and preview without writing to DB",
    )
    args = parser.parse_args()

    load_anatel(
        file_path=args.file_path,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
