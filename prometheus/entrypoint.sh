#!/bin/sh
# Render /etc/prometheus/prometheus.yml from prometheus.yml.tpl, substituting
# runtime-only values (currently: RAILWAY_DNS for the failover SECONDARY probe).
#
# Rationale: the Railway edge hostname is the only truly dynamic scrape target
# in this repo — everything else is stable service DNS inside the compose
# network. Keeping RAILWAY_DNS as a single env var (vs. committing the literal
# hostname into prometheus.yml) means a rotation requires one edit in
# docker-compose.yml instead of two (prometheus + setup_failover.sh).
#
# The scripts (scripts/setup_failover.sh, scripts/verify_failover.sh) already
# honour RAILWAY_DNS, so this change unifies the source of truth.
set -eu

: "${RAILWAY_DNS:=web-production-90b1f.up.railway.app}"

# Basic sanity: reject shell metacharacters / whitespace that would be
# meaningless in a DNS name and could break the sed substitution.
case "$RAILWAY_DNS" in
  *[!a-zA-Z0-9.-]*)
    echo "entrypoint.sh: invalid RAILWAY_DNS=${RAILWAY_DNS} (must match [a-zA-Z0-9.-]+)" >&2
    exit 2
    ;;
esac

echo "entrypoint.sh: rendering prometheus.yml with RAILWAY_DNS=${RAILWAY_DNS}"
sed "s|__RAILWAY_DNS__|${RAILWAY_DNS}|g" \
  /etc/prometheus/prometheus.yml.tpl \
  > /etc/prometheus/prometheus.yml

# Preserve upstream prom/prometheus default args. Any args passed to the
# container (docker compose `command:`) are appended via "$@".
exec /bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --web.console.libraries=/usr/share/prometheus/console_libraries \
  --web.console.templates=/usr/share/prometheus/consoles \
  "$@"
