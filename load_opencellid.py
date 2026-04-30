"""
load_opencellid.py – Download and import Brazilian cell towers from OpenCelliD.

OpenCelliD is the world's largest open database of cell towers.  This script
downloads the pre-built country extract for Brazil (MCC 724), transforms the
records to the platform's tower schema, and bulk-inserts them via TowerStore.

Requirements:
    - An OpenCelliD API token (free registration at https://opencellid.org)
    - Set the token via --token flag or OPENCELLID_TOKEN env var

Usage:
    python load_opencellid.py --token pk_xxxxxxx
    python load_opencellid.py --file 724.csv.gz          # use local file
    python load_opencellid.py --token pk_xxx --dry-run   # preview only
"""

import argparse
import csv
import gzip
import io
import os
import sys
import tempfile
from typing import Any, Dict, List

import requests

from tower_db import TowerStore

# Brazil MCC
BRAZIL_MCC = "724"

# Download URL template for country-specific cell data
DOWNLOAD_URL = (
    "https://opencellid.org/ocid/downloads"
    "?token={token}&type=mcc&file={mcc}.csv.gz"
)

# ── Operator mapping: MNC → operator name ────────────────────────
# Source: ITU / ANATEL MNC assignments for MCC 724
_MNC_OPERATOR = {
    "02": "TIM",
    "03": "TIM",
    "04": "TIM",
    "05": "Claro",
    "06": "Vivo",
    "10": "Vivo",
    "11": "Vivo",
    "23": "Vivo",
    "15": "Sercomtel",
    "16": "Oi",
    "31": "Oi",
    "30": "Oi",
    "32": "Algar",
    "33": "Algar",
    "34": "Algar",
    "00": "Nextel",
    "38": "Claro",
    "39": "Nextel",
    "01": "Vivo",
    "07": "CTBC",
    "08": "TIM",
    "24": "Amazonas",
    "37": "Aeiou",
    "54": "SEAE",
    "99": "Privado",
}

# ── Radio type → typical Brazilian frequency bands ───────────────
_RADIO_BANDS = {
    "GSM":   ["900MHz", "1800MHz"],
    "UMTS":  ["850MHz", "2100MHz"],
    "LTE":   ["700MHz", "1800MHz", "2600MHz"],
    "CDMA":  ["850MHz"],
    "NR":    ["3500MHz"],
    "NBIOT": ["700MHz"],
}

# ── Radio type → typical power (dBm) ────────────────────────────
_RADIO_POWER = {
    "GSM":   43.0,
    "UMTS":  43.0,
    "LTE":   46.0,
    "CDMA":  43.0,
    "NR":    46.0,
    "NBIOT": 40.0,
}

# Default tower height when not available (meters)
_DEFAULT_HEIGHT = 35.0

# Persistent cache directory for downloaded files
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".opencellid_cache")


def download_brazil_csv(token: str, dest_dir: str | None = None) -> str:
    """Download Brazil (MCC 724) cell tower CSV from OpenCelliD.

    Returns the path to the downloaded file.
    Falls back to a cached copy if rate-limited.
    Raises RuntimeError if the API returns an error and no cache exists.
    """
    cache_path = os.path.join(_CACHE_DIR, f"{BRAZIL_MCC}.csv.gz")
    url = DOWNLOAD_URL.format(token=token, mcc=BRAZIL_MCC)
    print(f"Downloading OpenCelliD data for Brazil (MCC {BRAZIL_MCC})...")

    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()

    # OpenCelliD returns JSON on error (invalid token, rate limit, etc.)
    content_type = resp.headers.get("Content-Type", "")
    if "json" in content_type or "text/html" in content_type:
        body = resp.content.decode("utf-8", errors="replace")[:500]
        # If rate-limited and we have a cached copy, use it
        if "RATE_LIMITED" in body and os.path.exists(cache_path):
            import datetime
            mtime = os.path.getmtime(cache_path)
            age = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  Rate-limited — using cached file from {age}")
            print(f"  {cache_path}")
            return cache_path
        raise RuntimeError(
            f"OpenCelliD returned an error instead of data.\n"
            f"  Content-Type: {content_type}\n"
            f"  Response: {body}\n"
            f"  → Check that your API token is valid. "
            f"Register at https://opencellid.org/ to get one."
        )

    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="opencellid_")

    gz_path = os.path.join(dest_dir, f"{BRAZIL_MCC}.csv.gz")
    total = 0
    with open(gz_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            f.write(chunk)
            total += len(chunk)

    # Verify the file is actually gzip by checking magic bytes
    with open(gz_path, "rb") as f:
        magic = f.read(2)
    if magic != b"\x1f\x8b":
        # Not gzip — probably a JSON/HTML error page
        with open(gz_path, "r", encoding="utf-8", errors="replace") as f:
            body = f.read(500)
        os.remove(gz_path)
        raise RuntimeError(
            f"Downloaded file is not valid gzip data.\n"
            f"  Content: {body}\n"
            f"  → Your API token may be invalid or rate-limited. "
            f"Register at https://opencellid.org/ to get a token."
        )

    size_mb = total / (1024 * 1024)
    print(f"  downloaded {size_mb:.1f} MB → {gz_path}")

    # Cache a persistent copy for rate-limit fallback
    os.makedirs(_CACHE_DIR, exist_ok=True)
    import shutil
    shutil.copy2(gz_path, cache_path)

    return gz_path


def _operator_from_mnc(mnc: str) -> str:
    # Try exact match, then zero-padded, then stripped
    return (_MNC_OPERATOR.get(mnc)
            or _MNC_OPERATOR.get(mnc.zfill(2))
            or _MNC_OPERATOR.get(mnc.lstrip("0") or "0")
            or f"MNC-{mnc}")


def parse_opencellid_csv(
    csv_path: str,
    *,
    limit: int = 0,
    min_samples: int = 2,
) -> List[Dict[str, Any]]:
    """Parse an OpenCelliD CSV (optionally gzipped) into tower dicts.

    Columns expected (per https://wiki.opencellid.org/wiki/Database_format):
        radio, mcc, net, area, cell, unit, lon, lat, range, samples,
        changeable, created, updated, averageSignal

    Args:
        csv_path: Path to the .csv or .csv.gz file.
        limit: Max towers to return (0 = all).
        min_samples: Skip cells with fewer measurement samples.
    """
    # Auto-detect gzip by magic bytes, not just file extension
    with open(csv_path, "rb") as fcheck:
        magic = fcheck.read(2)
    is_gzip = magic == b"\x1f\x8b"
    open_fn = gzip.open if is_gzip else open

    towers: List[Dict[str, Any]] = []
    seen_ids: set = set()
    skipped = 0

    _FIELDNAMES = [
        "radio", "mcc", "net", "area", "cell", "unit",
        "lon", "lat", "range", "samples", "changeable",
        "created", "updated", "averageSignal",
    ]

    with open_fn(csv_path, "rt", encoding="utf-8", errors="replace") as f:
        # OpenCelliD country extracts have no header row
        reader = csv.DictReader(f, fieldnames=_FIELDNAMES)
        for row in reader:
            # Filter: only Brazil
            if row.get("mcc", "") != BRAZIL_MCC:
                continue

            # Filter: minimum sample count for data quality
            samples = int(row.get("samples", "0"))
            if samples < min_samples:
                skipped += 1
                continue

            radio = row.get("radio", "LTE").upper()
            mnc = row.get("net", "")
            area = row.get("area", "")
            cell = row.get("cell", "")
            lat_str = row.get("lat", "")
            lon_str = row.get("lon", "")

            if not lat_str or not lon_str:
                skipped += 1
                continue

            lat = float(lat_str)
            lon = float(lon_str)

            # Sanity: must be within Brazil bounding box
            if not (-34.0 <= lat <= 6.0 and -74.0 <= lon <= -28.0):
                skipped += 1
                continue

            tower_id = f"OCID_{BRAZIL_MCC}_{mnc}_{area}_{cell}"
            if tower_id in seen_ids:
                continue
            seen_ids.add(tower_id)

            operator = _operator_from_mnc(mnc)
            bands = _RADIO_BANDS.get(radio, ["Unknown"])
            power = _RADIO_POWER.get(radio, 43.0)

            towers.append({
                "id": tower_id,
                "lat": lat,
                "lon": lon,
                "height_m": _DEFAULT_HEIGHT,
                "operator": operator,
                "bands": bands,
                "power_dbm": power,
            })

            if limit and len(towers) >= limit:
                break

    print(f"  parsed {len(towers)} towers ({skipped} skipped for low quality/out-of-bounds)")
    return towers


# ── Frequency mapping for signal samples ─────────────────────────
# Use the first band of each radio type as the representative carrier
# frequency for the averageSignal label.
_RADIO_FREQ_HZ = {
    "GSM":   900e6,
    "UMTS":  2.1e9,
    "LTE":   1.8e9,
    "CDMA":  850e6,
    "NR":    3.5e9,
    "NBIOT": 700e6,
}


# NOTE: An earlier revision exposed parse_opencellid_signal_samples() and
# load_opencellid_signal_samples() to ingest the OpenCelliD <averageSignal>
# field as ML training labels. Empirically (verified 2026-04-30 with token
# pk.e560… against the BR MCC=724 free-tier CSV), that field is 0 for
# 100 %% of Brazilian rows — it is paid/contributor-tier data only. The
# extraction path was removed rather than kept as a no-op that misleads
# operators. OpenCelliD remains the source for tower coordinates
# (lat/lon, range, MCC/MNC) via parse_opencellid_csv() above.


def load_opencellid(
    *,
    token: str | None = None,
    file_path: str | None = None,
    limit: int = 0,
    min_samples: int = 2,
    batch_size: int = 5000,
    dry_run: bool = False,
    use_copy: bool = False,
) -> int:
    """Main entry point: download (if needed), parse, and load towers.

    Returns the total number of towers loaded.
    """
    if file_path:
        if not os.path.exists(file_path):
            print(f"ERROR: file not found: {file_path}", file=sys.stderr)
            return 0
        csv_path = file_path
    elif token:
        csv_path = download_brazil_csv(token)
    else:
        print("ERROR: provide --token or --file", file=sys.stderr)
        return 0

    print(f"\nParsing OpenCelliD data from {csv_path} ...")
    towers = parse_opencellid_csv(csv_path, limit=limit, min_samples=min_samples)

    if not towers:
        print("No towers parsed.")
        return 0

    if dry_run:
        print(f"\n[DRY RUN] Would load {len(towers)} towers. Sample:")
        for t in towers[:5]:
            print(f"  {t['id']:40s}  {t['operator']:10s}  "
                  f"({t['lat']:.4f}, {t['lon']:.4f})  {','.join(t['bands'])}")
        return len(towers)

    store = TowerStore()
    print(f"\nLoading {len(towers)} towers into {store.backend} database...")

    loaded = 0
    if use_copy and store.backend == "postgresql":
        print("  using PostgreSQL COPY for bulk import...")
        loaded = store.copy_from_towers(towers)
        print(f"  COPY loaded {loaded} towers")
    else:
        for i in range(0, len(towers), batch_size):
            batch = towers[i : i + batch_size]
            written = store.upsert_many(batch)
            loaded += written
            print(f"  batch {i // batch_size + 1}: "
                  f"{written} towers (total {loaded}/{len(towers)})")

    print(f"\nDone. {loaded} OpenCelliD towers loaded. "
          f"Total towers in DB: {store.count()}")
    return loaded


def main():
    parser = argparse.ArgumentParser(
        description="Load Brazilian cell towers from OpenCelliD into the database"
    )
    parser.add_argument(
        "--token",
        default=os.getenv("OPENCELLID_TOKEN"),
        help="OpenCelliD API token (or set OPENCELLID_TOKEN env var)",
    )
    parser.add_argument(
        "--file", dest="file_path",
        help="Path to a local 724.csv.gz file (skips download)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max towers to load (0 = all)",
    )
    parser.add_argument(
        "--min-samples", type=int, default=1,
        help="Skip cells with fewer than N measurement samples (default: 1)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000,
        help="DB insert batch size (default: 5000)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and preview without writing to DB",
    )
    parser.add_argument(
        "--use-copy", action="store_true",
        help="Use PostgreSQL COPY for faster bulk import (PG only)",
    )
    args = parser.parse_args()

    if not args.token and not args.file_path:
        parser.error("Provide --token or --file (or set OPENCELLID_TOKEN env var)")

    load_opencellid(
        token=args.token,
        file_path=args.file_path,
        limit=args.limit,
        min_samples=args.min_samples,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        use_copy=args.use_copy,
    )


if __name__ == "__main__":
    main()
