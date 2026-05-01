# Docker Compose Secrets

This directory holds **file-based secrets** consumed by Docker Compose's
`secrets:` feature. Each file contains a single secret value (no trailing
newline). Secrets are mounted at `/run/secrets/<name>` inside containers
and **never appear in `docker inspect` output**.

## Quick start

```bash
cd secrets/
bash init_secrets.sh          # creates empty placeholder files
# fill each file with the real value, e.g.:
echo -n 'postgresql://telecom:pass@host:5432/towers' > database_url
chmod 600 *                    # restrict to owner-read only
cd .. && docker compose up -d
```

## How it works

1. `docker-compose.yml` declares a top-level `secrets:` mapping to these files.
2. Each service lists the secrets it needs; Compose bind-mounts them to
   `/run/secrets/<name>` (read-only, mode 0444 inside the container).
3. `entrypoint.sh` reads `/run/secrets/*` into environment variables so the
   Python application code requires **no changes** to its `os.getenv()` calls.
4. After loading, the entrypoint unsets the env vars and the Python app scrubs
   `os.environ`, so secrets do not persist in `/proc/*/environ`.

## Files

| File                    | Env var equivalent         |
|-------------------------|----------------------------|
| `database_url`          | `DATABASE_URL`             |
| `postgres_password`     | `POSTGRES_PASSWORD`        |
| `aws_access_key_id`     | `AWS_ACCESS_KEY_ID`        |
| `aws_secret_access_key` | `AWS_SECRET_ACCESS_KEY`    |
| `stripe_secret_key`     | `STRIPE_SECRET_KEY`        |
| `stripe_webhook_secret` | `STRIPE_WEBHOOK_SECRET`    |
| `stripe_price_pro`      | `STRIPE_PRICE_PRO`         |
| `stripe_price_enterprise`| `STRIPE_PRICE_ENTERPRISE` |
| `ses_smtp_username`     | `SES_SMTP_USERNAME`        |
| `ses_smtp_password`     | `SES_SMTP_PASSWORD`        |
| `valid_api_keys`        | `VALID_API_KEYS`           |
| `slack_webhook_url`     | `SLACK_WEBHOOK_URL`        |
| `audit_target_hmac_pepper` | `AUDIT_TARGET_HMAC_PEPPER` |
| `admin_totp_secrets`    | `ADMIN_TOTP_SECRETS`       |

> **Security**: All files are git-ignored. Never commit secret values.

> **`audit_target_hmac_pepper`** keys the HMAC that pseudonymises
> business-sensitive identifiers (e.g. `tower_id`) in the audit log,
> protecting them from leaking via DB backups, admin reads, or legal
> subpoenas. Generate once and rotate only in coordination with a full
> audit-log purge — rotating invalidates tenants' ability to correlate
> their own historical entries. To create:
>
> ```bash
> openssl rand -hex 32 > secrets/audit_target_hmac_pepper
> chmod 600 secrets/audit_target_hmac_pepper
> ```
>
> Leaving the file empty disables HMACing (cleartext logging) — only
> acceptable in local dev / CI.

> **`admin_totp_secrets`** holds the per-admin TOTP seeds used by the
> step-up MFA on `/admin/impersonate/*` and `/admin/sales/tenants/*`.
> Format: comma-separated `email:base32secret` pairs. Generate a new
> base32 secret per admin and provision it into an authenticator app
> (Google Authenticator, 1Password, Authy). To create / rotate one entry:
>
> ```bash
> SECRET=$(python3 -c 'import base64,os;print(base64.b32encode(os.urandom(20)).decode().rstrip("="))')
> echo "ops@example.com:${SECRET}" > secrets/admin_totp_secrets
> chmod 600 secrets/admin_totp_secrets
> # show otpauth:// URI for the admin to scan in their authenticator
> python3 -c "from urllib.parse import quote;print(f'otpauth://totp/TelecomTowerPower:ops@example.com?secret=${SECRET}&issuer=TelecomTowerPower')"
> ```
>
> If the file is empty or the admin's email is not present, both routes
> return 503/403 — the operator must enrol before they can impersonate.

## Production sync (EC2)

In production, secrets are sourced from **AWS SSM Parameter Store** (SecureString) and synced to this directory by GitHub Actions workflows:

| Workflow | Secrets synced |
|---|---|
| `update-ec2-stripe-secrets.yml` | `stripe_secret_key`, `stripe_webhook_secret` |
| `update-ec2-alerting-secrets.yml` | `slack_webhook_url`, `ses_smtp_username`, `ses_smtp_password` |

Remote execution uses `aws ssm send-command` (no SSH keys in CI). Containers consuming changed secrets are restarted automatically. See [docs-site/docs/operations/runbook.md](../docs-site/docs/operations/runbook.md).
