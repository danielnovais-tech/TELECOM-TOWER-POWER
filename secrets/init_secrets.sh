#!/bin/sh
# Create empty secret files for Docker Compose secrets.
# Fill each file with the actual secret value (single line, no trailing newline).
# Files are world-readable (0644) so containers running as non-root users
# (e.g. Grafana uid 472) can read them in non-swarm compose mode.
# The secrets/ directory is .gitignored — do not commit real values.
set -e
cd "$(dirname "$0")"

for name in \
    database_url \
    postgres_password \
    aws_access_key_id \
    aws_secret_access_key \
    stripe_secret_key \
    stripe_webhook_secret \
    stripe_price_pro \
    stripe_price_enterprise \
    ses_smtp_username \
    ses_smtp_password \
    valid_api_keys \
    slack_webhook_url \
    grafana_admin_password
do
    if [ ! -f "$name" ]; then
        touch "$name"
        echo "created $name"
    fi
    chmod 644 "$name"
done

echo "Done. Fill each file with the secret value, then run: docker compose up"
