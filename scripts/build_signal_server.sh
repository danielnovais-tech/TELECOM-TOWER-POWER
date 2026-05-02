#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Build Signal-Server (GPL-2.0) for the rf_engines adapter.
#
# Usage:
#   bash scripts/build_signal_server.sh [PREFIX]
#
# UPSTREAM
# --------
# The original `Cloud-RF/Signal-Server` repository was DELETED by its
# author in 2023; that GitHub URL now resolves to a historical README
# only and contains no source. The community moved to forks. We build
# from W3AXL/Signal-Server (most recently updated, ~2025), which is
# itself a descendant of valderez → CloudRF before deletion. Override
# with $SIGNAL_SERVER_REPO if you prefer N9OZB or another fork.
#
# IMPORTANT — licensing:
#   Signal-Server is GPL-2.0 (per LICENSE.txt and runtime banner).
#   We DO NOT bundle the resulting binary into the platform's
#   container image. Operators run this script on the ECS host (or a
#   sidecar build container) at provisioning time and upload the
#   binary to S3 alongside the ITU digital maps. The Python adapter
#   loads it from $SIGNAL_SERVER_BIN at request time only.
#
# CAVEAT — wire format:
#   Upstream Signal-Server has a *flag-based* CLI that emits PPM
#   bitmaps + text reports keyed off `-o basename`. The Python
#   adapter in `rf_engines/signal_server_engine.py` currently sends a
#   JSON envelope expecting `--json` support, which only exists in a
#   (non-existent) patched fork. Until that gap is closed (either
#   patch upstream or replace _call_subprocess with a flag-based call
#   that parses the site-report), the adapter is a placeholder:
#   `is_available()` returns False because no `--json`-aware binary
#   will be on $PATH. This script still produces the canonical
#   `signalserver` / `signalserverHD` binaries so an operator can
#   experiment manually with the real flags.
set -euo pipefail

PREFIX="${1:-/usr/local}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

UPSTREAM="${SIGNAL_SERVER_REPO:-https://github.com/W3AXL/Signal-Server.git}"

echo "[signal-server] cloning $UPSTREAM ..."
git clone --depth 1 "$UPSTREAM" "$WORKDIR/ss"

# Build deps (Debian/Ubuntu). Skip silently if not root or already present.
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq || true
    sudo apt-get install -y --no-install-recommends \
        build-essential cmake g++ libbz2-dev imagemagick libspdlog-dev \
        >/dev/null 2>&1 || true
fi

(
    cd "$WORKDIR/ss"
    # Modern W3AXL build is cmake-based; produces ./build/signalserver,
    # signalserverHD, signalserverLIDAR.
    mkdir -p build
    cd build
    cmake ../src
    make -j"$(nproc 2>/dev/null || echo 2)"
)

install -d "$PREFIX/bin"
for b in signalserver signalserverHD signalserverLIDAR; do
    src="$WORKDIR/ss/build/$b"
    if [[ -x "$src" ]]; then
        install -m 0755 "$src" "$PREFIX/bin/$b"
        echo "[signal-server] installed: $PREFIX/bin/$b"
    fi
done

"$PREFIX/bin/signalserverHD" 2>&1 | head -n 5 || true

cat <<'EOF'

NEXT STEPS
----------
1. Convert SRTM .hgt tiles to .sdf with utils/srtm2sdf-hd (HD = 30m,
   SRTM1) and stage them where $SIGNAL_SERVER_BIN can read with -sdf.
2. Set $SIGNAL_SERVER_BIN=/usr/local/bin/signalserverHD on the ECS task.
3. (Optional) $SIGNAL_SERVER_MODEL=itm  # itm | itwom | hata | ericsson
4. The Python adapter expects a `--json` interface that this binary
   does NOT provide upstream. To wire it in for real, either:
       (a) patch the fork to read a JSON envelope on stdin and emit
           basic_loss_db on stdout; or
       (b) replace the adapter's _call_subprocess with a flag-based
           call (`-lat -lon -rla -rlo -txh -rxh -f -pm ...`) that
           parses the resulting `<basename>-site_report.txt`.
   Until then, GET /coverage/engines will keep reporting
   `signal-server: available=false`.

REMINDER: Signal-Server is GPL-2.0. Do not redistribute the binary
inside our container images.
EOF
