#!/bin/sh
# Railway mounts volumes as root:root. Running as root avoids permission issues.
set -e
mkdir -p /prometheus
# Railway injects $PORT for public routing; bind Prometheus to it (default 9090).
LISTEN_PORT="${PORT:-9090}"
exec /bin/prometheus \
    --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/prometheus \
    --web.console.libraries=/usr/share/prometheus/console_libraries \
    --web.console.templates=/usr/share/prometheus/consoles \
    --web.listen-address=0.0.0.0:${LISTEN_PORT} \
    "$@"
