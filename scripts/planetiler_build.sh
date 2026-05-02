#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Build vector tiles for Brazil using Planetiler.
#
# Why: rf-signals / signal-server outputs are most useful when overlaid
# on a base map with administrative boundaries, terrain shading, and
# (eventually) Planet Labs satellite imagery. Planetiler renders the
# whole planet in <2h on a laptop; for Brazil-only it's <30 min.
#
# Output: $OUT_DIR/brazil.pmtiles  (PMTiles — single-file, served
# directly by Caddy via the existing pmtiles plugin or by the SPA
# via maplibre-gl).
set -euo pipefail

OUT_DIR="${OUT_DIR:-./tiles}"
PLANETILER_VERSION="${PLANETILER_VERSION:-0.8.4}"
JAR="${PLANETILER_JAR:-$OUT_DIR/planetiler.jar}"

mkdir -p "$OUT_DIR"
if [[ ! -f "$JAR" ]]; then
    echo "[planetiler] downloading $PLANETILER_VERSION ..."
    curl -fsSL -o "$JAR" \
        "https://github.com/onthegomap/planetiler/releases/download/v${PLANETILER_VERSION}/planetiler.jar"
fi

# OpenMapTiles profile is the de-facto base layer set (admin, places,
# transportation, water, landcover) — same vector schema the SPA's
# maplibre style sheet already consumes.
echo "[planetiler] building Brazil tiles into $OUT_DIR ..."
java -Xmx4g -jar "$JAR" \
    --area=brazil \
    --download \
    --output="$OUT_DIR/brazil.pmtiles" \
    --force

ls -lh "$OUT_DIR/brazil.pmtiles"
echo "[planetiler] done — serve with: caddy file_server { browse } and route /tiles/* to $OUT_DIR"
