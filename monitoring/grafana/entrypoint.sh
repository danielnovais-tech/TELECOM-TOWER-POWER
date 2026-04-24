#!/bin/sh
# Railway mounts volumes as root:root. Chown and run Grafana's own run.sh as root.
set -e
mkdir -p /var/lib/grafana/plugins /var/log/grafana
chown -R grafana:root /var/lib/grafana /var/log/grafana /etc/grafana 2>/dev/null || true
# Railway injects $PORT for public routing; tell Grafana to listen on it.
export GF_SERVER_HTTP_PORT="${PORT:-3000}"
exec /run.sh "$@"
