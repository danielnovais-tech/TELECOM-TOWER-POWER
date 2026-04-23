#!/bin/sh
# Railway mounts volumes as root:root. Running as root avoids permission issues.
set -e
mkdir -p /prometheus
exec /bin/prometheus \
    --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/prometheus \
    --web.console.libraries=/usr/share/prometheus/console_libraries \
    --web.console.templates=/usr/share/prometheus/consoles \
    "$@"
