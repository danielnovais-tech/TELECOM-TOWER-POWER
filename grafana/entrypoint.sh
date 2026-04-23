#!/bin/sh
# Load secret files into environment for Grafana provisioning.
# Grafana provisioning YAML supports $__env{VAR} interpolation only for real env vars,
# not *__FILE pointers — so we export them here before starting.
set -e

if [ -r /run/secrets/slack_webhook_url ]; then
  SLACK_WEBHOOK_URL="$(cat /run/secrets/slack_webhook_url 2>/dev/null || true)"
  export SLACK_WEBHOOK_URL
fi

# Sensible defaults so provisioning doesn't fail if vars are unset.
export SLACK_CHANNEL="${SLACK_CHANNEL:-#alerts}"
export ALERT_EMAIL_TO="${ALERT_EMAIL_TO:-daniel.novais@sempreceub.com}"

exec /run.sh "$@"
