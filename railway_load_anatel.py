#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Load ANATEL towers from S3 CSV into Railway PostgreSQL."""
import csv, gzip, io, os, sys, tempfile
import psycopg2
from psycopg2.extras import execute_values

S3_BUCKET = "telecom-tower-power-results"
S3_KEY = "data/anatel_towers.csv.gz"
S3_REGION = "sa-east-1"

def download_from_s3(dest):
    import boto3
    s3 = boto3.client("s3", region_name=S3_REGION)
    print(f"Downloading s3://{S3_BUCKET}/{S3_KEY} ...")
    s3.download_file(S3_BUCKET, S3_KEY, dest)
    print(f"  saved to {dest}")

def parse_csv(path):
    towers = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bands = [b.strip() for b in row["bands"].split(",")]
            towers.append((
                row["id"], float(row["lat"]), float(row["lon"]),
                float(row["height_m"]), row["operator"],
                bands, float(row["power_dbm"])
            ))
    print(f"  Parsed {len(towers):,} towers")
    return towers

def bulk_insert(conn, towers, batch_size=5000):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM towers")
        before = cur.fetchone()[0]
        print(f"  Towers before: {before:,}")
        inserted = 0
        for i in range(0, len(towers), batch_size):
            batch = towers[i:i+batch_size]
            execute_values(cur, """
                INSERT INTO towers (id, lat, lon, height_m, operator, bands, power_dbm)
                VALUES %s ON CONFLICT (id) DO NOTHING
            """, batch, page_size=batch_size)
            inserted += len(batch)
            if inserted % 20000 == 0 or inserted == len(towers):
                print(f"  ... {inserted:,} / {len(towers):,}")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM towers")
        after = cur.fetchone()[0]
        print(f"  Towers after: {after:,}")
        print(f"  Net new: {after - before:,}")

def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit("ERROR: DATABASE_URL not set")
    print(f"DB: {db_url[:40]}...")
    tmp = tempfile.mktemp(suffix=".csv.gz")
    download_from_s3(tmp)
    towers = parse_csv(tmp)
    conn = psycopg2.connect(db_url)
    print(f"Inserting {len(towers):,} ANATEL towers...")
    bulk_insert(conn, towers)
    conn.close()
    os.remove(tmp)
    print("Done!")

if __name__ == "__main__":
    main()
