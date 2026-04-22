"""
load_towers.py
Pre-load towers from a CSV file into the TELECOM TOWER POWER API.

Usage:
    python load_towers.py                                        # defaults
    python load_towers.py --csv towers_brazil.csv                # custom CSV
    python load_towers.py --url https://api.example.com          # custom URL
    python load_towers.py --api-key pro_abcdef                   # custom key
    python load_towers.py towers_brazil.csv http://localhost:8000 # positional (legacy)
"""

import argparse
import csv
import os
import sys
import requests

DEFAULT_CSV = "towers_brazil.csv"
DEFAULT_URL = os.getenv("LOAD_TOWERS_URL", "http://localhost:8000")
DEFAULT_KEY = os.getenv("LOAD_TOWERS_API_KEY", "demo-key-pro-001")


def load_towers(csv_path: str, base_url: str, api_key: str):
    url = f"{base_url.rstrip('/')}/towers"
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}

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


def parse_args():
    parser = argparse.ArgumentParser(description="Load towers from CSV into the API")
    parser.add_argument("positional_csv", nargs="?", default=None, help=argparse.SUPPRESS)
    parser.add_argument("positional_url", nargs="?", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--csv", dest="csv_path", default=None, help=f"Path to towers CSV (default: {DEFAULT_CSV})")
    parser.add_argument("--url", dest="base_url", default=None, help=f"API base URL (default: {DEFAULT_URL})")
    parser.add_argument("--api-key", dest="api_key", default=DEFAULT_KEY, help=f"API key (default: {DEFAULT_KEY})")
    args = parser.parse_args()

    csv_path = args.csv_path or args.positional_csv or DEFAULT_CSV
    base_url = args.base_url or args.positional_url or DEFAULT_URL
    return csv_path, base_url, args.api_key


if __name__ == "__main__":
    csv_path, base_url, api_key = parse_args()
    print(f"Loading towers from {csv_path} → {base_url}\n")
    load_towers(csv_path, base_url, api_key)
