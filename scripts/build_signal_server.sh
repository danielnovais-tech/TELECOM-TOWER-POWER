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
# WIRE FORMAT — TTP JSON shim:
#   Upstream Signal-Server has a flag-based CLI that writes PPM
#   bitmaps + a text site-report keyed off `-o basename`. To bridge
#   it with the `signal_server_engine` adapter we apply the local
#   patch `scripts/signal_server_json.patch` (GPL-2.0, audited
#   against W3AXL master 7f6242a) which adds a single `-json` flag.
#   When set, PPA mode additionally prints one JSON line to stdout:
#       {"basic_loss_db": <float>, "free_space_loss_db": <float>,
#        "distance": <float>, "frequency_mhz": <float>,
#        "model": <int>, "engine": "signal-server"}
#   Without `-json` the binary is byte-for-byte upstream behaviour.
#   The adapter still gates on $SIGNAL_SERVER_JSON_FORK=1 so
#   `is_available()` only returns True for binaries built by this
#   script (or another patched build).
set -euo pipefail

PREFIX="${1:-/usr/local}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

UPSTREAM="${SIGNAL_SERVER_REPO:-https://github.com/W3AXL/Signal-Server.git}"

echo "[signal-server] cloning $UPSTREAM ..."
git clone --depth 1 "$UPSTREAM" "$WORKDIR/ss"

# Apply TTP JSON-stdout shim. See scripts/signal_server_json.patch header
# for SPDX/provenance. Patch is verified against W3AXL master
# 7f6242afb3685ff31d9ad14062b80d692ee56327; if upstream HEAD has moved
# and the patch fails to apply, pin the clone to that SHA.
PATCH_FILE="$(cd "$(dirname "$0")" && pwd)/signal_server_json.patch"
if [[ -f "$PATCH_FILE" ]]; then
    echo "[signal-server] applying TTP JSON shim: $PATCH_FILE"
    (cd "$WORKDIR/ss" && git apply --check "$PATCH_FILE" \
        && git apply "$PATCH_FILE") \
        || { echo "[signal-server] FATAL: patch did not apply; upstream HEAD likely drifted."; exit 1; }
else
    echo "[signal-server] WARN: $PATCH_FILE not found; building unpatched binary (adapter will stay disabled)."
fi

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
4. Set $SIGNAL_SERVER_JSON_FORK=1 to opt the adapter in. The adapter
   will invoke the binary with flag-based PPA mode plus `-json` and
   parse the JSON line emitted on stdout (see scripts/signal_server_json.patch).
5. Smoke test the patched binary:
       signalserverHD -sdf /path/to/sdf -lat <txlat> -lon <txlon> \
           -rla <rxlat> -rlo <rxlon> -txh 30 -rxh 2 -f 900 \
           -pm 1 -m -o /tmp/ss-smoke -json
   The LAST line of stdout MUST be valid JSON containing
   "basic_loss_db".

REMINDER: Signal-Server is GPL-2.0. Do not redistribute the binary
inside our container images. The patch is also GPL-2.0-or-later.
EOF
