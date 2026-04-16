#!/usr/bin/env python3
"""
import_towers.py – Migrate all towers from a source PostgreSQL database
(AWS ECS) to a target PostgreSQL database (Railway) with streaming batches,
UPSERT semantics, progress reporting, and post-import validation.

Usage:
    # Use explicit connection strings
    python3 import_towers.py --source postgresql://... --target postgresql://...

    # Use environment variables (AWS_DATABASE_URL → source, DATABASE_URL → target)
    python3 import_towers.py --source-env AWS --target-env RAILWAY

    # Dry-run: count towers only, no import
    python3 import_towers.py --source postgresql://... --target postgresql://... --dry-run

    # Verbose output (per-batch timing, sample rows)
    python3 import_towers.py --source postgresql://... --target postgresql://... --verbose

Environment variables used by --source-env / --target-env:
    AWS     → AWS_DATABASE_URL
    RAILWAY → DATABASE_URL  (or RAILWAY_DATABASE_URL)
    SOURCE  → SOURCE_DATABASE_URL
    TARGET  → TARGET_DATABASE_URL
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from contextlib import contextmanager
from typing import Generator, List, Optional, Tuple

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.extensions
except ImportError:
    print(
        "ERROR: psycopg2 is not installed.\n"
        "  Run:  pip install psycopg2-binary",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────────

BATCH_SIZE = 1_000          # rows per SELECT / INSERT batch
PROGRESS_EVERY = 10_000     # print progress every N towers
MAX_RETRIES = 3             # transient-failure retry attempts
RETRY_DELAY_S = 5.0         # seconds between retries
CONNECT_TIMEOUT_S = 30      # psycopg2 connect_timeout (seconds)
SPOT_CHECK_COUNT = 5        # number of random towers to verify after import

# ── Environment-variable presets ─────────────────────────────────────────────

_ENV_PRESETS: dict[str, list[str]] = {
    "AWS":     ["AWS_DATABASE_URL", "AWS_DB_URL"],
    "RAILWAY": ["DATABASE_URL", "RAILWAY_DATABASE_URL", "RAILWAY_DB_URL"],
    "SOURCE":  ["SOURCE_DATABASE_URL", "SOURCE_DB_URL"],
    "TARGET":  ["TARGET_DATABASE_URL", "TARGET_DB_URL", "DATABASE_URL"],
}


def _resolve_env_preset(preset: str) -> str:
    """Return the first non-empty env var for the given preset name."""
    key = preset.upper()
    candidates = _ENV_PRESETS.get(key, [key + "_DATABASE_URL", key])
    for var in candidates:
        val = os.getenv(var, "").strip()
        if val:
            return val
    tried = ", ".join(candidates)
    raise SystemExit(
        f"ERROR: --source-env/--target-env preset '{preset}' found no value.\n"
        f"  Tried env vars: {tried}\n"
        f"  Set one of them before running."
    )


# ── URL normalisation (mirrors tower_db.py) ──────────────────────────────────

def _normalise_url(url: str) -> str:
    """Convert postgres:// and SQLAlchemy async variants to postgresql://."""
    url = url.strip()
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# ── Connection helpers ───────────────────────────────────────────────────────

def _connect(dsn: str, label: str) -> psycopg2.extensions.connection:
    """Open a psycopg2 connection with a timeout and friendly error message."""
    try:
        conn = psycopg2.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S)
        conn.autocommit = False
        return conn
    except psycopg2.OperationalError as exc:
        raise SystemExit(
            f"ERROR: Could not connect to {label} database.\n"
            f"  DSN (truncated): {dsn[:60]}...\n"
            f"  Reason: {exc}"
        ) from exc


@contextmanager
def _transaction(conn: psycopg2.extensions.connection):
    """Context manager: commit on success, rollback on exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Source: streaming tower reader ───────────────────────────────────────────

def _count_source(conn: psycopg2.extensions.connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM towers")
        row = cur.fetchone()
        return row[0] if row else 0


def _stream_towers(
    conn: psycopg2.extensions.connection,
    batch_size: int = BATCH_SIZE,
) -> Generator[List[Tuple], None, None]:
    """Yield batches of raw tower tuples from the source database.

    Uses a server-side cursor (named cursor) so the full result set is
    never materialised in memory — safe for 140k+ rows.

    Each tuple: (id, lat, lon, height_m, operator, bands, power_dbm)
    """
    cursor_name = f"import_towers_cursor_{int(time.time())}"
    with conn.cursor(name=cursor_name) as cur:
        cur.itersize = batch_size
        cur.execute(
            "SELECT id, lat, lon, height_m, operator, bands, power_dbm "
            "FROM towers ORDER BY id"
        )
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            yield rows


# ── Target: ensure table exists ──────────────────────────────────────────────

def _ensure_table(conn: psycopg2.extensions.connection, verbose: bool = False) -> None:
    """Create the towers table in the target DB if it does not exist."""
    with _transaction(conn):
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS towers (
                    id         TEXT PRIMARY KEY,
                    lat        DOUBLE PRECISION NOT NULL,
                    lon        DOUBLE PRECISION NOT NULL,
                    height_m   DOUBLE PRECISION NOT NULL,
                    operator   TEXT NOT NULL,
                    bands      TEXT NOT NULL,
                    power_dbm  DOUBLE PRECISION NOT NULL DEFAULT 43.0
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_towers_operator
                ON towers(operator)
            """)
            # Spatial index (best-effort — requires cube + earthdistance)
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS cube")
                cur.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_towers_coords
                    ON towers USING gist(ll_to_earth(lat, lon))
                """)
                if verbose:
                    print("  [target] earthdistance spatial index ready.")
            except Exception as exc:
                conn.rollback()
                if verbose:
                    print(f"  [target] Spatial index skipped ({exc}). Continuing.")


def _count_target(conn: psycopg2.extensions.connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM towers")
        row = cur.fetchone()
        return row[0] if row else 0


# ── Target: batch UPSERT ─────────────────────────────────────────────────────

_UPSERT_SQL = """
    INSERT INTO towers (id, lat, lon, height_m, operator, bands, power_dbm)
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        lat       = EXCLUDED.lat,
        lon       = EXCLUDED.lon,
        height_m  = EXCLUDED.height_m,
        operator  = EXCLUDED.operator,
        bands     = EXCLUDED.bands,
        power_dbm = EXCLUDED.power_dbm
"""


def _normalise_bands(bands_value) -> str:
    """Ensure bands is stored as a JSON string regardless of source format.

    The source DB may store bands as:
      - a TEXT column containing JSON  → '["700MHz","1800MHz"]'
      - a TEXT[] PostgreSQL array      → '{700MHz,1800MHz}'
      - a Python list (already parsed) → ['700MHz', '1800MHz']
    """
    if isinstance(bands_value, list):
        return json.dumps(bands_value)
    if isinstance(bands_value, str):
        s = bands_value.strip()
        # PostgreSQL array literal: {val1,val2,...}
        if s.startswith("{") and not s.startswith("{\""):
            inner = s[1:-1]
            items = [v.strip().strip('"') for v in inner.split(",") if v.strip()]
            return json.dumps(items)
        # Already JSON
        return s
    # Fallback
    return json.dumps([])


def _upsert_batch(
    conn: psycopg2.extensions.connection,
    rows: List[Tuple],
    verbose: bool = False,
) -> Tuple[int, List[str]]:
    """UPSERT a batch of raw source rows into the target.

    Returns (rows_written, error_ids).
    """
    values = []
    error_ids: List[str] = []

    for row in rows:
        try:
            tower_id, lat, lon, height_m, operator, bands_raw, power_dbm = row
            bands = _normalise_bands(bands_raw)
            values.append((
                str(tower_id),
                float(lat),
                float(lon),
                float(height_m),
                str(operator),
                bands,
                float(power_dbm) if power_dbm is not None else 43.0,
            ))
        except Exception as exc:
            tid = row[0] if row else "unknown"
            error_ids.append(str(tid))
            if verbose:
                print(f"  [warn] Skipping tower {tid}: {exc}")

    if not values:
        return 0, error_ids

    with _transaction(conn):
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, _UPSERT_SQL, values, page_size=BATCH_SIZE
            )
            written = len(values)

    return written, error_ids


# ── Retry wrapper ────────────────────────────────────────────────────────────

def _upsert_batch_with_retry(
    target_dsn: str,
    rows: List[Tuple],
    verbose: bool = False,
) -> Tuple[int, List[str]]:
    """Attempt _upsert_batch up to MAX_RETRIES times on transient failures.

    Opens a fresh connection per attempt so a broken connection is not reused.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            conn = _connect(target_dsn, "target (retry)")
            try:
                written, errors = _upsert_batch(conn, rows, verbose=verbose)
                return written, errors
            finally:
                conn.close()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                print(
                    f"  [warn] Transient error on attempt {attempt}/{MAX_RETRIES}: {exc}. "
                    f"Retrying in {RETRY_DELAY_S}s..."
                )
                time.sleep(RETRY_DELAY_S)
        except Exception as exc:
            # Non-transient — surface immediately
            raise exc

    raise RuntimeError(
        f"Batch failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
    )


# ── Validation: spot-check ───────────────────────────────────────────────────

def _spot_check(
    source_conn: psycopg2.extensions.connection,
    target_conn: psycopg2.extensions.connection,
    n: int = SPOT_CHECK_COUNT,
    verbose: bool = False,
) -> Tuple[int, int]:
    """Pick n random tower IDs from source and verify they exist in target.

    Returns (found, total_checked).
    """
    with source_conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM towers ORDER BY random() LIMIT %s", (n,)
        )
        sample_ids = [row[0] for row in cur.fetchall()]

    if not sample_ids:
        return 0, 0

    found = 0
    with target_conn.cursor() as cur:
        for tid in sample_ids:
            cur.execute("SELECT 1 FROM towers WHERE id = %s", (tid,))
            exists = cur.fetchone() is not None
            if exists:
                found += 1
            if verbose:
                status = "✓" if exists else "✗ MISSING"
                print(f"  spot-check {tid}: {status}")

    return found, len(sample_ids)


# ── Main import logic ────────────────────────────────────────────────────────

def run_import(
    source_dsn: str,
    target_dsn: str,
    dry_run: bool = False,
    verbose: bool = False,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Stream all towers from source → target.

    Returns the total number of towers written (0 on dry-run).
    """
    source_dsn = _normalise_url(source_dsn)
    target_dsn = _normalise_url(target_dsn)

    # ── Connect ──────────────────────────────────────────────────
    print("Connecting to source database...")
    src_conn = _connect(source_dsn, "source")
    src_conn.autocommit = False  # named cursor requires a transaction

    print("Connecting to target database...")
    tgt_conn = _connect(target_dsn, "target")

    try:
        # ── Pre-flight counts ─────────────────────────────────────
        print("\nCounting towers...")
        total_source = _count_source(src_conn)
        total_target_before = _count_target(tgt_conn)

        print(f"  Source towers : {total_source:>10,}")
        print(f"  Target towers : {total_target_before:>10,}  (before import)")

        if total_source == 0:
            print("\nSource database has 0 towers — nothing to import.")
            return 0

        if dry_run:
            print(
                f"\n[DRY RUN] Would import up to {total_source:,} towers "
                f"({total_source - total_target_before:+,} net change). "
                f"No data written."
            )
            return 0

        # ── Ensure target schema ──────────────────────────────────
        print("\nEnsuring target schema...")
        _ensure_table(tgt_conn, verbose=verbose)
        print("  Target schema ready.")

        # ── Stream & import ───────────────────────────────────────
        print(f"\nImporting towers in batches of {batch_size:,}...")
        start_time = time.monotonic()
        last_progress_time = start_time

        imported = 0
        all_errors: List[str] = []
        batch_num = 0

        for batch in _stream_towers(src_conn, batch_size=batch_size):
            batch_num += 1
            batch_start = time.monotonic()

            written, errors = _upsert_batch_with_retry(
                target_dsn, batch, verbose=verbose
            )
            imported += written
            all_errors.extend(errors)

            now = time.monotonic()
            elapsed = now - start_time

            # Per-batch verbose timing
            if verbose:
                batch_elapsed = now - batch_start
                rate = written / batch_elapsed if batch_elapsed > 0 else 0
                print(
                    f"  batch {batch_num:>4d}: {written:>5,} towers  "
                    f"({batch_elapsed:.2f}s, {rate:,.0f} t/s)"
                )

            # Periodic progress line
            if (
                imported % PROGRESS_EVERY < batch_size
                or imported >= total_source
            ):
                pct = (imported / total_source * 100) if total_source else 0
                overall_rate = imported / elapsed if elapsed > 0 else 0
                eta_s = (
                    (total_source - imported) / overall_rate
                    if overall_rate > 0 and imported < total_source
                    else 0
                )
                eta_str = f"  ETA ~{eta_s:.0f}s" if eta_s > 1 else ""
                print(
                    f"  Imported {imported:>10,} / {total_source:,} towers "
                    f"({pct:.1f}%)  [{overall_rate:,.0f} t/s]{eta_str}"
                )
                last_progress_time = now

        # ── Final timing ──────────────────────────────────────────
        total_elapsed = time.monotonic() - start_time
        overall_rate = imported / total_elapsed if total_elapsed > 0 else 0

        print(f"\n{'─' * 60}")
        print(f"Import complete in {total_elapsed:.1f}s  ({overall_rate:,.0f} towers/sec)")

        # ── Post-import counts ────────────────────────────────────
        total_target_after = _count_target(tgt_conn)
        net_new = total_target_after - total_target_before

        print(f"\nValidation:")
        print(f"  Source towers  : {total_source:>10,}")
        print(f"  Target before  : {total_target_before:>10,}")
        print(f"  Target after   : {total_target_after:>10,}")
        print(f"  Net new        : {net_new:>+10,}")
        print(f"  Rows written   : {imported:>10,}")

        if all_errors:
            print(
                f"\n  [warn] {len(all_errors)} towers had parse errors and were skipped:"
            )
            for eid in all_errors[:20]:
                print(f"    - {eid}")
            if len(all_errors) > 20:
                print(f"    ... and {len(all_errors) - 20} more")

        # ── Spot-check ────────────────────────────────────────────
        print(f"\nSpot-checking {SPOT_CHECK_COUNT} random towers...")
        found, checked = _spot_check(
            src_conn, tgt_conn, n=SPOT_CHECK_COUNT, verbose=verbose
        )
        if checked > 0:
            print(f"  {found}/{checked} spot-check towers found in target.")
            if found < checked:
                missing = checked - found
                print(
                    f"  [warn] {missing} tower(s) not found in target — "
                    f"possible partial import or ID mismatch."
                )
        else:
            print("  No towers available for spot-check.")

        # ── Summary verdict ───────────────────────────────────────
        print()
        if total_target_after >= total_source and (not all_errors or len(all_errors) < 10):
            print("✓ Import successful. Target is in sync with source.")
        elif total_target_after > total_target_before:
            print(
                f"⚠ Partial import: {total_target_after:,} / {total_source:,} towers "
                f"in target. Re-run to complete."
            )
        else:
            print("✗ Import may have failed — target count unchanged. Check errors above.")

        return imported

    finally:
        src_conn.close()
        tgt_conn.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="import_towers.py",
        description=(
            "Migrate all towers from a source PostgreSQL database (AWS ECS) "
            "to a target PostgreSQL database (Railway) using streaming batches "
            "and UPSERT semantics."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Explicit connection strings
  python3 import_towers.py \\
      --source postgresql://user:pw@aws-host:5432/towers \\
      --target postgresql://user:pw@railway-host:5432/railway

  # Use environment variables (AWS_DATABASE_URL → source, DATABASE_URL → target)
  python3 import_towers.py --source-env AWS --target-env RAILWAY

  # Dry-run: count only, no import
  python3 import_towers.py --source-env AWS --target-env RAILWAY --dry-run

  # Verbose output with per-batch timing
  python3 import_towers.py --source-env AWS --target-env RAILWAY --verbose

Environment variable presets (--source-env / --target-env):
  AWS     → AWS_DATABASE_URL
  RAILWAY → DATABASE_URL  (or RAILWAY_DATABASE_URL)
  SOURCE  → SOURCE_DATABASE_URL
  TARGET  → TARGET_DATABASE_URL
        """,
    )

    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "--source",
        metavar="DSN",
        help="Source database connection string (postgresql://...)",
    )
    src_group.add_argument(
        "--source-env",
        metavar="PRESET",
        help=(
            "Resolve source DSN from an env-var preset: "
            "AWS, RAILWAY, SOURCE, or any custom prefix (e.g. MY → MY_DATABASE_URL)"
        ),
    )

    tgt_group = parser.add_mutually_exclusive_group(required=True)
    tgt_group.add_argument(
        "--target",
        metavar="DSN",
        help="Target database connection string (postgresql://...)",
    )
    tgt_group.add_argument(
        "--target-env",
        metavar="PRESET",
        help=(
            "Resolve target DSN from an env-var preset: "
            "AWS, RAILWAY, TARGET, or any custom prefix"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count towers only — do not write anything to the target database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-batch timing, spot-check details, and skipped-row warnings.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        metavar="N",
        help=f"Rows per SELECT/INSERT batch (default: {BATCH_SIZE:,})",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Resolve DSNs
    source_dsn: str = (
        args.source
        if args.source
        else _resolve_env_preset(args.source_env)
    )
    target_dsn: str = (
        args.target
        if args.target
        else _resolve_env_preset(args.target_env)
    )

    # Safety: refuse to import from and to the same database
    if _normalise_url(source_dsn) == _normalise_url(target_dsn):
        print(
            "ERROR: Source and target connection strings resolve to the same database.\n"
            "  Aborting to prevent data corruption.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Redact passwords for display
    def _redact(dsn: str) -> str:
        import re
        return re.sub(r"(:)[^:@]+(@)", r"\1***\2", dsn)

    print("=" * 60)
    print("TELECOM TOWER POWER — Database Import")
    print("=" * 60)
    print(f"  Source : {_redact(source_dsn)}")
    print(f"  Target : {_redact(target_dsn)}")
    print(f"  Mode   : {'DRY RUN (no writes)' if args.dry_run else 'LIVE IMPORT'}")
    print(f"  Batch  : {args.batch_size:,} rows")
    print()

    try:
        run_import(
            source_dsn=source_dsn,
            target_dsn=target_dsn,
            dry_run=args.dry_run,
            verbose=args.verbose,
            batch_size=args.batch_size,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Import may be partial.", file=sys.stderr)
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\nFATAL ERROR: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
