#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Build Cloud-RF/Signal-Server (GPLv3) for the rf_engines adapter.
#
# Usage:
#   bash scripts/build_signal_server.sh [PREFIX]
#
# IMPORTANT — licensing:
#   Signal-Server is GPLv3. We DO NOT bundle the resulting binary into
#   the platform's container image. Operators run this script on the
#   ECS host (or a sidecar build container) at provisioning time and
#   upload the binary to S3 alongside the ITU digital maps. The Python
#   adapter loads it from $SIGNAL_SERVER_BIN at request time only.
set -euo pipefail

PREFIX="${1:-/usr/local}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "[signal-server] cloning ..."
git clone --depth 1 https://github.com/Cloud-RF/Signal-Server.git "$WORKDIR/ss"

# Build deps (Debian/Ubuntu). Skip silently if not root or already present.
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq || true
    sudo apt-get install -y --no-install-recommends \
        build-essential libbz2-dev g++ make >/dev/null 2>&1 || true
fi

(
    cd "$WORKDIR/ss"
    # Upstream Makefile target produces signalserverHD (HD = high-resolution
    # SDF tile support — what we want for Brazilian SRTM 1-arcsecond data).
    make signalserverHD
)

install -d "$PREFIX/bin"
install -m 0755 "$WORKDIR/ss/signalserverHD" "$PREFIX/bin/signalserverHD"
echo "[signal-server] installed: $PREFIX/bin/signalserverHD"
"$PREFIX/bin/signalserverHD" -? 2>&1 | head -n 5 || true

cat <<'EOF'

NEXT STEPS
----------
1. Provision SDF tiles for Brazil (already done for the platform's
   srtm_elevation cache — Signal-Server reads the same .sdf format).
2. Set $SIGNAL_SERVER_BIN=/usr/local/bin/signalserverHD on the ECS task.
3. (Optional) $SIGNAL_SERVER_MODEL=itm  # itm | itwom | hata | ericsson
4. Restart the API container; GET /coverage/engines should now show
   "signal-server" with available=true.

REMINDER: Signal-Server is GPLv3. Do not redistribute the binary
inside our container images.
EOF
