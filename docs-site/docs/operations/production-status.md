# Production Status

Snapshot of the production environment. Last verified: **April 2026**.

## Infrastructure

- **EC2 + Docker Compose** stable on instance `i-045166a6a1933f507` (`sa-east-1`, user `ubuntu`, path `/home/ubuntu/TELECOM-TOWER-POWER`).
- **Caddy** listens on `:80` behind the ALB (which terminates TLS). Routing rules:
    - `api.telecomtowerpower.com.br` → Railway (`https://web-production-90b1f.up.railway.app`) with `Host` header override.
    - `www.*` / `app.*` API paths (`/api/*`, `/health*`, `/calculate*`, `/towers*`, `/batch*`, `/jobs*`, `/docs*`, `/openapi.json`, `/stripe*`, `/usage*`, `/api-key*`, `/signup*`, `/login*`, `/profile*`, `/portal*`, `/analyze*`, `/plan_repeater*`, `/export_report*`, `/bedrock*`, `/srtm*`) → Railway.
    - `/webhook*` → local Stripe handler on `localhost:8001`.
    - `/grafana*` → local Grafana on `localhost:3001`.
    - Fallback → React SPA on `localhost:3000`.
    - `docs.telecomtowerpower.com.br` → static MkDocs build served from `/srv/docs` inside the Caddy container.
- **ALB direct bypass** (no Caddy):
    - `monitoring.telecomtowerpower.com.br` → target group `ttp-grafana-tg:3001`.
    - `prometheus.telecomtowerpower.com.br` → target group `ttp-prometheus-tg:9090`.

## CI/CD

Three hardened workflows drive production operations:

| Workflow | Purpose |
|---|---|
| `deploy-ec2-docker.yml` | Build + push API image, SSM-deploy Docker Compose stack |
| `update-ec2-stripe-secrets.yml` | Sync Stripe secrets to EC2 via SSM |
| `update-ec2-alerting-secrets.yml` | Sync Slack webhook + SES SMTP credentials to EC2 |

All three use:

- **BuildKit cache mount** — `RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip` in the Dockerfile.
- **`concurrency:` groups** — prevent overlapping runs.
- **Retry loop on health checks** — 30 iterations × 2 s against `localhost:3001/api/health` (Grafana) after secret sync.

All **11** workflows under `.github/workflows/` are pinned to **Node 24**.

## Observability

Grafana provisioning (verified live via `/api/v1/provisioning/*`, `provenance: file`):

- **Contact points**
    - `email-alerts` → `daniel.novais@sempreceub.com` (AWS SES SMTP)
    - `slack-alerts` → `#alerts` (via webhook)
- **Notification policies**
    - `severity=critical` → Slack (`continue: true`) + Email, `group_wait: 15s`, `repeat_interval: 1h`
    - `severity=warning` → Slack + Email, `group_wait: 30s`, `repeat_interval: 2h`
- **Alert rules (active)**
    - `high-5xx-rate`: `sum(rate(http_requests_total{status=~"5.."}[1m])) * 60 > 10`

## Secrets

- Never committed to git (`.gitignore` covers `secrets/`).
- Stored in AWS SSM Parameter Store (SecureString).
- Synced to EC2 at `/home/ubuntu/TELECOM-TOWER-POWER/secrets/` via GitHub Actions.
- Consumed by Docker Compose via the top-level `secrets:` mapping and mounted read-only at `/run/secrets/<name>`.

Current files on EC2:

| File | Size | Consumer |
|---|---|---|
| `slack_webhook_url` | 81 B | Grafana Alertmanager |
| `ses_smtp_username` | 20 B | Grafana Alertmanager (SMTP) |
| `ses_smtp_password` | 44 B | Grafana Alertmanager (SMTP) |
| `stripe_secret_key` | — | API container |
| `stripe_webhook_secret` | — | Stripe webhook service |

## Deployment

- **Zero-downtime** — `aws ssm send-command` triggers `docker compose pull && docker compose up -d` on the EC2 host. Compose performs rolling replacement per service.
- **No SSH keys in CI** — all remote execution goes through SSM.
- **Railway/Railpack compatibility preserved** — `railway.json`, `Procfile`, and `Dockerfile` remain valid; API image can be redeployed to Railway without changes.
