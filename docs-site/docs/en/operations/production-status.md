# Production Status

Snapshot of the production environment. Last verified: **April 2026**.

## Infrastructure

- **EC2 + Docker Compose** stable on instance `i-045166a6a1933f507` (`sa-east-1`, user `ubuntu`, path `/home/ubuntu/TELECOM-TOWER-POWER`).
- **Instance type**: `t3.small` (2 vCPU, 2 GB RAM, 20 GB gp3).
    - Live load average: `0.13 / 0.14 / 0.15` on 2 cores — CPU well under capacity.
    - Memory: `1.9 GB total`, `~800 MB available` with `~200 MB swap in use` — stable but tight.
    - Disk: `15 GB / 19 GB used (82 %)` — monitored by the `disk-space-low` alert.
- **Caddy** listens on `:80` behind the ALB (which terminates TLS). Routing rules:
    - `api.telecomtowerpower.com.br` → Railway edge (`i1fuknjg.up.railway.app`).
      **TLS termination depends on Route 53 failover state** — see below.
    - `www.*` / `app.*` API paths (`/api/*`, `/health*`, `/calculate*`, `/towers*`, `/batch*`, `/jobs*`, `/docs*`, `/openapi.json`, `/stripe*`, `/usage*`, `/api-key*`, `/signup*`, `/login*`, `/profile*`, `/portal*`, `/analyze*`, `/plan_repeater*`, `/export_report*`, `/bedrock*`, `/srtm*`) → Railway.
    - `/webhook*` → local Stripe handler on `localhost:8001`.
    - `/grafana*` → local Grafana on `localhost:3001`.
    - Fallback → React SPA on `localhost:3000`.
    - `docs.telecomtowerpower.com.br` → static MkDocs build served from `/srv/docs` inside the Caddy container.

!!! info "TLS termination for `api.telecomtowerpower.com.br`"
    - **Normal mode (PRIMARY, ALB healthy):** client → **ALB terminates TLS** with ACM cert → Caddy on `:80` → Railway over HTTPS (Caddy re-originates TLS to the Railway edge).
    - **Failover mode (SECONDARY, ALB unhealthy):** client → **Railway edge terminates TLS** with the Let's Encrypt cert Railway issues for the custom domain (requires the `_railway-verify.api` TXT record in Route 53 to stay valid).
    - During an incident, check which mode you are in (`dig api.telecomtowerpower.com.br +short`) before assuming where TLS is terminating.
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
- **Alert rules**

| UID | Severity | Trigger |
|---|---|---|
| `high-5xx-rate` | critical | `>10` server errors/min for 2 min |
| `HighRateLimitHits` | warning | `>5` 429/min for 2 min |
| `HighAPILatencyP95` | warning | p95 latency `>1.5 s` for 5 min |
| `BatchQueueStuck` | critical | pending jobs with zero throughput for 10 min |
| `batch-job-failures` | critical | `>1` batch job failure in 5 min |
| `LowDiskSpace` | warning | root disk `>85 %` for 5 min |
| `memory-pressure` | warning | swap `>512 MB` for 10 min |

> Host metrics are exposed via a `node-exporter` container (`network_mode: host`) reachable from Prometheus at `host.docker.internal:9100`.

## Secrets

- Never committed to git (`.gitignore` covers `secrets/`).
- Stored in AWS SSM Parameter Store (SecureString).
- Synced to EC2 at `/home/ubuntu/TELECOM-TOWER-POWER/secrets/` via GitHub Actions.
- Consumed by Docker Compose via the top-level `secrets:` mapping and mounted read-only at `/run/secrets/<name>`.

| File | Size | Consumer |
|---|---|---|
| `slack_webhook_url` | 81 B | Grafana / Alertmanager |
| `ses_smtp_username` | 20 B | Grafana / Alertmanager (SMTP) |
| `ses_smtp_password` | 44 B | Grafana / Alertmanager (SMTP) |
| `stripe_secret_key` | — | API container |
| `stripe_webhook_secret` | — | Stripe webhook service |

## Deployment

- **Zero-downtime** — `aws ssm send-command` triggers `docker compose pull && docker compose up -d` on the EC2 host. Compose performs rolling replacement per service.
- **No SSH keys in CI** — all remote execution goes through SSM.
- **Railway / Railpack compatibility preserved** — `railway.json`, `Procfile`, and `Dockerfile` remain valid; the API image can be redeployed to Railway without changes.
