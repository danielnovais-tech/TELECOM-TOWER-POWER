#!/usr/bin/env bash
# Build rfsignals-cli and install into ${PREFIX:-$HOME/.local}/bin.
#
# Usage:
#   bash scripts/build_rf_signals.sh            # → $HOME/.local/bin
#   PREFIX=/usr/local sudo -E bash scripts/build_rf_signals.sh
#
# Optional S3 publish (used by the rf-signals-publish CI workflow):
#   S3_PUBLISH_URI=s3://telecom-tower-power-results/bin/rfsignals-cli \
#     bash scripts/build_rf_signals.sh
#   When set, the script also uploads the freshly built binary to that
#   key with `--acl public-read` and writes a SHA-tagged sibling
#   (rfsignals-cli-<sha256>) for rollback. Requires `aws` on PATH and
#   AWS creds in the environment. No-op when S3_PUBLISH_URI is unset.
#
# The Python adapter at rf_engines/rf_signals_engine.py looks for the
# binary in this order:
#   1. $RF_SIGNALS_BIN
#   2. /usr/local/bin/rfsignals-cli
#   3. shutil.which("rfsignals-cli")
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
crate_dir="$here/rf_signals"
prefix="${PREFIX:-$HOME/.local}"
target_dir="$prefix/bin"

if ! command -v cargo >/dev/null 2>&1; then
    echo "error: cargo not found; install Rust toolchain first" >&2
    echo "  Ubuntu/Debian:  sudo apt-get install -y rustc cargo" >&2
    echo "  Other:          https://rustup.rs" >&2
    exit 1
fi

cd "$crate_dir"
cargo build --release

mkdir -p "$target_dir"
install -m 0755 "target/release/rfsignals-cli" "$target_dir/rfsignals-cli"

echo "installed: $target_dir/rfsignals-cli"
echo "ok — set RF_SIGNALS_BIN=$target_dir/rfsignals-cli (or add $target_dir to PATH)"

# ── Optional: publish to S3 (CI hook) ──────────────────────────────
if [ -n "${S3_PUBLISH_URI:-}" ]; then
    if ! command -v aws >/dev/null 2>&1; then
        echo "error: S3_PUBLISH_URI set but aws CLI not on PATH" >&2
        exit 2
    fi
    bin_path="$target_dir/rfsignals-cli"
    sha=$(sha256sum "$bin_path" | awk '{print $1}')
    short=${sha:0:12}
    sibling="${S3_PUBLISH_URI%/*}/rfsignals-cli-${short}"

    # Public-read because the binary is built from this repo's
    # clean-room sources only — no copyleft, no secret material.
    # The container fetches it via plain HTTPS at boot, no AWS creds.
    aws s3 cp "$bin_path" "$S3_PUBLISH_URI" \
        --acl public-read \
        --content-type application/octet-stream \
        --cache-control "public, max-age=300" \
        --metadata "sha256=$sha"
    aws s3 cp "$bin_path" "$sibling" \
        --acl public-read \
        --content-type application/octet-stream \
        --cache-control "public, max-age=31536000, immutable" \
        --metadata "sha256=$sha"

    echo "published: $S3_PUBLISH_URI (sha256=$sha)"
    echo "rollback : $sibling"
fi

