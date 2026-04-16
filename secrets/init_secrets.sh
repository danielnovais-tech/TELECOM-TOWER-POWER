#!/bin/sh
# Create empty secret files for Docker Compose secrets.
# Fill in each file with the actual secret value (single line, no newline).
# Run: chmod 600 secrets/*
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
    slack_webhook_url
do
    if [ ! -f "$name" ]; then
        touch "$name"
        chmod 600 "$name"
        echo "created $name"
    fi
done

echo "Done. Fill each file with the secret value, then run: docker compose up"
