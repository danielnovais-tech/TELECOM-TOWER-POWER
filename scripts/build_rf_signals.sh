#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Build the rf-signals CLI binary used by rf_engines/rf_signals_engine.py.
#
# Usage:
#   bash scripts/build_rf_signals.sh [PREFIX]
#
# Default PREFIX is /usr/local. The resulting binary is copied to
# $PREFIX/bin/rfsignals-cli and is detected automatically by the
# Python adapter via $RF_SIGNALS_BIN or $PATH.
#
# Why a custom build instead of `cargo install`?
#   The upstream repository (thebracket/rf-signals) ships the engine
#   as a library; we wrap it in a tiny CLI shim that speaks the JSON
#   protocol expected by rf_signals_engine.py. The shim sources are
#   under scripts/rf_signals_cli/ in this repo (vendored, ~80 lines
#   of Rust) — adding it to the upstream repo would couple their
#   release cadence to ours.
set -euo pipefail

PREFIX="${1:-/usr/local}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "[rf-signals] cloning into $WORKDIR ..."
git clone --depth 1 https://github.com/thebracket/rf-signals.git "$WORKDIR/rf-signals"

# Vendor our CLI shim alongside the upstream crate.
SHIM_SRC="$(dirname "$0")/rf_signals_cli"
if [[ ! -d "$SHIM_SRC" ]]; then
    echo "[rf-signals] CLI shim sources missing at $SHIM_SRC" >&2
    exit 2
fi
cp -r "$SHIM_SRC" "$WORKDIR/rf-signals/cli-shim"

echo "[rf-signals] building release binary ..."
(
    cd "$WORKDIR/rf-signals/cli-shim"
    cargo build --release --locked
)

BIN="$WORKDIR/rf-signals/cli-shim/target/release/rfsignals-cli"
if [[ ! -x "$BIN" ]]; then
    echo "[rf-signals] expected binary not produced: $BIN" >&2
    exit 3
fi

install -d "$PREFIX/bin"
install -m 0755 "$BIN" "$PREFIX/bin/rfsignals-cli"
echo "[rf-signals] installed: $PREFIX/bin/rfsignals-cli"
"$PREFIX/bin/rfsignals-cli" --version || true
