"""
load_towers.py
Pre-load towers from a CSV file into the TELECOM TOWER POWER API.

Usage:
    python load_towers.py                          # defaults: towers_brazil.csv → localhost:8000
    python load_towers.py towers_brazil.csv        # custom CSV
    python load_towers.py towers_brazil.csv https://telecom-tower-power-api.onrender.com
"""

import csv
import sys
import requests

DEFAULT_CSV = "towers_brazil.csv"
DEFAULT_URL = "http://localhost:8000"
API_KEY = "demo-key-pro-001"


def load_towers(csv_path: str, base_url: str):
    url = f"{base_url.rstrip('/')}/towers"
    headers = {"Content-Type": "application/json", "X-API-Key": API_KEY}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        ok = 0
        fail = 0
        for row in reader:
            tower = {
                "id": row["id"].strip(),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "height_m": float(row["height_m"]),
                "operator": row["operator"].strip(),
                "bands": [b.strip() for b in row["bands"].split(",")],
                "power_dbm": float(row["power_dbm"]),
            }
            resp = requests.post(url, json=tower, headers=headers, timeout=15)
            if resp.status_code == 201:
                print(f"  + {tower['id']:12s}  {tower['operator']:6s}  ({tower['lat']:.4f}, {tower['lon']:.4f})  {','.join(tower['bands'])}")
                ok += 1
            else:
                print(f"  ! {tower['id']:12s}  FAILED  {resp.status_code}: {resp.text}")
                fail += 1

    print(f"\nLoaded {ok} towers ({fail} failed) into {base_url}")


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    base_url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL
    print(f"Loading towers from {csv_path} → {base_url}\n")
    load_towers(csv_path, base_url)
