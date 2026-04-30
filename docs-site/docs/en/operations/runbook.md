# Runbook

Operational procedures for the production EC2 environment.

## Quick reference

| Resource | Value |
|---|---|
| EC2 instance ID | `i-045166a6a1933f507` |
| Region | `sa-east-1` |
| Instance type | `t3.small` (2 vCPU, 2 GB RAM, 20 GB gp3) |
| SSH user | `ubuntu` |
| Project path on host | `/home/ubuntu/TELECOM-TOWER-POWER` |
| Secrets path on host | `/home/ubuntu/TELECOM-TOWER-POWER/secrets/` |
| Caddy entry point | `:80` (ALB terminates TLS) |
| Railway API (custom domain) | `https://api.telecomtowerpower.com.br` |
| Railway edge target | `web-production-90b1f.up.railway.app` (may rotate — re-check in Railway UI before editing DNS) |

!!! danger "Do NOT remove the Railway ownership TXT record"
    Route 53 holds a `_railway-verify.api.telecomtowerpower.com.br` TXT record.
    Railway uses it to keep the Let's Encrypt cert for `api.telecomtowerpower.com.br` valid.
    If any automation or cleanup script deletes this record, the SECONDARY leg
    of the DNS failover will break with TLS errors the next time the ALB is
    unhealthy. It must never be edited from Terraform / external-dns / manual
    DNS cleanup without first re-pinning a new value from the Railway UI.

!!! note "Detect drift before an incident"
    Run `scripts/verify_failover.sh` (read-only, safe in cron/CI). It checks
    that the SECONDARY CNAME still matches the current Railway edge, that the
    `_railway-verify` TXT record is present, and that the edge still serves a
    cert covering `api.telecomtowerpower.com.br`. If Railway rotates the edge,
    re-run `scripts/setup_failover.sh` with the new value:
    `RAILWAY_DNS=<new>.up.railway.app ./scripts/setup_failover.sh`.

## Common operations

### Deploy latest `main` to EC2

```bash
gh workflow run deploy-ec2-docker.yml
```

The workflow opens an ephemeral SSH ingress on the EC2 security group, pushes a one-shot key via EC2 Instance Connect, then over SSH runs `git pull && docker compose build && docker compose up -d` on the host. Rolling replacement is performed per service — no manual downtime window needed.

### Sync Stripe secrets to EC2

```bash
gh workflow run update-ec2-stripe-secrets.yml
```

Reads `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from GitHub Actions secrets and writes them to `/home/ubuntu/TELECOM-TOWER-POWER/secrets/`. Containers consuming these secrets are restarted automatically.

### Sync alerting secrets (Slack + SES) to EC2

```bash
gh workflow run update-ec2-alerting-secrets.yml
```

Syncs `SLACK_WEBHOOK_URL`, `SES_SMTP_USERNAME`, `SES_SMTP_PASSWORD`. After sync, Grafana is restarted and a health check is retried up to 30 × 2 s against `localhost:3001/api/health`.

### Reload Caddy after a config change

```bash
gh workflow run deploy-caddy.yml
```

Copies the `Caddyfile` to EC2 and runs `caddy reload`. Verifies HTTP 200 on `www.*`, `app.*`, `api.*`, `monitoring.*`, and `prometheus.*`.

## Troubleshooting

### Grafana health check fails during the alerting-secrets workflow

- Ensure the port is **3001** on the EC2 host (not 3000 — that’s the React frontend).
- SSH in and check: `docker compose logs grafana --tail=50`.
- Verify the secret files exist and are non-empty: `ls -la /home/ubuntu/TELECOM-TOWER-POWER/secrets/`.

### 502 on `api.telecomtowerpower.com.br`

- First check Railway status — the Caddy config proxies 100 % of API traffic there.
- Check Caddy logs on EC2: `docker compose logs caddy --tail=100`.
- Confirm the ALB target is healthy: AWS console → Target Groups → `ttp-caddy-tg`.

### 5xx alert fires

The `high-5xx-rate` alert triggers when the API emits more than 10 errors/minute over a 1-minute window. Steps:

1. Check the Grafana dashboard for the affected endpoint.
2. `docker compose logs api --tail=200` on EC2, or check Railway logs if traffic is routed there.
3. If the spike is from a specific customer, check rate limits in `key_store.json`.

### Disk-space-low alert fires

The EC2 root fs is 20 GB. When utilisation passes 85 %, this alert fires. Cleanup steps:

```bash
# SSH in, then:
docker system prune -af --filter "until=168h"   # images >7 days old
docker builder prune -af
sudo journalctl --vacuum-time=7d
sudo du -h /var/lib/docker/containers | sort -h | tail
```

If the disk still fills within weeks, increase the EBS volume (`aws ec2 modify-volume …`) and run `sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1`.

### Memory-pressure alert fires

On `t3.small` (2 GB RAM) swap is expected under bursts, but sustained usage over 512 MB for 10+ minutes means real pressure. Options:

1. `docker stats --no-stream` to find the heaviest container.
2. Temporary relief: restart the worst offender.
3. Lasting relief: resize to `t3.medium` (4 GB) via `aws ec2 modify-instance-attribute`.

### Monitoring subdomain returns 404 / 502

- `monitoring.*` and `prometheus.*` bypass Caddy and hit ALB target groups directly.
- Verify targets are healthy: `ttp-grafana-tg` (port 3001), `ttp-prometheus-tg` (port 9090).
- If the EC2 instance was replaced, re-register it with `scripts/manage_ec2_alb.sh register`.

## Security reminders

- Secrets are **never** committed. If a secret leaks into git, rotate it immediately; prefer rotation over history-rewrite.
- All remote EC2 execution goes through SSM; no SSH keys are configured in CI.
- Secret files on EC2 are mounted read-only at `/run/secrets/<name>` inside containers.
