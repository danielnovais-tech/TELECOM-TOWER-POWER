#!/bin/sh
# Railway mounts volumes as root:root. Chown and run Grafana's own run.sh as root.
set -e
mkdir -p /var/lib/grafana/plugins /var/log/grafana
chown -R grafana:root /var/lib/grafana /var/log/grafana /etc/grafana 2>/dev/null || true
exec /run.sh "$@"
