#!/usr/bin/env bash
# Build rfsignals-cli and install into ${PREFIX:-$HOME/.local}/bin.
#
# Usage:
#   bash scripts/build_rf_signals.sh            # → $HOME/.local/bin
#   PREFIX=/usr/local sudo -E bash scripts/build_rf_signals.sh
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
