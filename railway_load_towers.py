#!/usr/bin/env python3
"""One-shot script to load OpenCelliD Brazil towers into Railway PostgreSQL.

Downloads 724.csv.gz from S3, parses it, and bulk-inserts via COPY.
Designed to run inside Railway container via `railway ssh`.
"""
import csv
import gzip
import io
import os
import subprocess
import sys
import tempfile

import psycopg2
from psycopg2.extras import execute_values

BRAZIL_MCC = "724"
S3_BUCKET = "telecom-tower-power-results"
S3_KEY = "data/724.csv.gz"
S3_REGION = "sa-east-1"

_MNC_OPERATOR = {
    "02": "TIM", "03": "TIM", "04": "TIM", "08": "TIM",
    "05": "Claro", "38": "Claro",
    "06": "Vivo", "10": "Vivo", "11": "Vivo", "23": "Vivo", "01": "Vivo",
    "15": "Sercomtel",
    "16": "Oi", "31": "Oi", "30": "Oi",
    "32": "Algar", "33": "Algar", "34": "Algar",
    "00": "Nextel", "39": "Nextel",
    "07": "CTBC", "24": "Amazonas", "37": "Aeiou", "54": "SEAE", "99": "Privado",
}

_RADIO_BANDS = {
    "GSM":   ["900MHz", "1800MHz"],
    "UMTS":  ["850MHz", "2100MHz"],
    "LTE":   ["700MHz", "1800MHz", "2600MHz"],
    "CDMA":  ["850MHz"],
    "NR":    ["3500MHz"],
    "NBIOT": ["700MHz"],
}

_RADIO_POWER = {
    "GSM": 43.0, "UMTS": 43.0, "LTE": 46.0,
    "CDMA": 43.0, "NR": 46.0, "NBIOT": 40.0,
}

_DEFAULT_HEIGHT = 35.0
_FIELDNAMES = [
    "radio", "mcc", "net", "area", "cell", "unit",
    "lon", "lat", "range", "samples", "changeable",
    "created", "updated", "averageSignal",
]


def download_from_s3(dest_path):
    """Download 724.csv.gz from S3 using boto3 or AWS CLI."""
    try:
        import boto3
        s3 = boto3.client("s3", region_name=S3_REGION)
        print(f"Downloading s3://{S3_BUCKET}/{S3_KEY} ...")
        s3.download_file(S3_BUCKET, S3_KEY, dest_path)
        print(f"  saved to {dest_path}")
        return True
    except Exception as e:
        print(f"boto3 failed ({e}), trying AWS CLI...")

    try:
        subprocess.check_call([
            "aws", "s3", "cp",
            f"s3://{S3_BUCKET}/{S3_KEY}", dest_path,
            "--region", S3_REGION,
        ])
        return True
    except Exception as e2:
        print(f"AWS CLI also failed: {e2}")
        return False


def parse_csv(csv_path, min_samples=2):
    """Parse OpenCelliD CSV into tower dicts."""
    with gzip.open(csv_path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, fieldnames=_FIELDNAMES)
        towers = []
        seen = set()
        skipped = 0

        for row in reader:
            if row.get("mcc", "") != BRAZIL_MCC:
                continue
            samples = int(row.get("samples", "0"))
            if samples < min_samples:
                skipped += 1
                continue

            lat_s, lon_s = row.get("lat", ""), row.get("lon", "")
            if not lat_s or not lon_s:
                skipped += 1
                continue

            lat, lon = float(lat_s), float(lon_s)
            if not (-34.0 <= lat <= 6.0 and -74.0 <= lon <= -28.0):
                skipped += 1
                continue

            radio = row.get("radio", "LTE").upper()
            mnc = row.get("net", "")
            area = row.get("area", "")
            cell = row.get("cell", "")
            tid = f"OCID_{BRAZIL_MCC}_{mnc}_{area}_{cell}"
            if tid in seen:
                continue
            seen.add(tid)

            op = _MNC_OPERATOR.get(mnc) or _MNC_OPERATOR.get(mnc.zfill(2)) or f"MNC-{mnc}"
            bands = _RADIO_BANDS.get(radio, ["Unknown"])
            power = _RADIO_POWER.get(radio, 43.0)

            towers.append((tid, lat, lon, _DEFAULT_HEIGHT, op, bands, power))

        print(f"  Parsed {len(towers):,} towers ({skipped:,} skipped)")
        return towers


def ensure_table(conn):
    """Create the towers table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS towers (
                id TEXT PRIMARY KEY,
                lat DOUBLE PRECISION NOT NULL,
                lon DOUBLE PRECISION NOT NULL,
                height_m DOUBLE PRECISION NOT NULL DEFAULT 35.0,
                operator TEXT NOT NULL DEFAULT '',
                bands TEXT[] NOT NULL DEFAULT '{}',
                power_dbm DOUBLE PRECISION NOT NULL DEFAULT 43.0
            )
        """)
    conn.commit()


def bulk_insert(conn, towers, batch_size=5000):
    """Insert towers using execute_values for speed."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM towers")
        before = cur.fetchone()[0]
        print(f"  Towers in DB before: {before:,}")

        inserted = 0
        for i in range(0, len(towers), batch_size):
            batch = towers[i:i + batch_size]
            sql = """
                INSERT INTO towers (id, lat, lon, height_m, operator, bands, power_dbm)
                VALUES %s
                ON CONFLICT (id) DO NOTHING
            """
            execute_values(cur, sql, batch, page_size=batch_size)
            inserted += len(batch)
            if inserted % 20000 == 0 or inserted == len(towers):
                print(f"  ... {inserted:,} / {len(towers):,}")

        conn.commit()
        cur.execute("SELECT COUNT(*) FROM towers")
        after = cur.fetchone()[0]
        print(f"  Towers in DB after: {after:,}")
        print(f"  Net new: {after - before:,}")


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    print(f"DB: {db_url[:40]}...")

    # Download
    tmp = tempfile.mktemp(suffix=".csv.gz")
    if not download_from_s3(tmp):
        sys.exit(1)

    # Parse
    print("Parsing OpenCelliD data...")
    towers = parse_csv(tmp)
    if not towers:
        print("No towers parsed!")
        sys.exit(1)

    # Load
    print(f"Connecting to PostgreSQL...")
    conn = psycopg2.connect(db_url)
    ensure_table(conn)

    print(f"Inserting {len(towers):,} towers...")
    bulk_insert(conn, towers)
    conn.close()

    # Cleanup
    os.remove(tmp)
    print("Done!")


if __name__ == "__main__":
    main()
