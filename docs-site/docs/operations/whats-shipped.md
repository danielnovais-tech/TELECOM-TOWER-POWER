# What's Shipped

> Cumulative inventory of every production feature and security item shipped to **TELECOM TOWER POWER**. All items below are live, tested, and documented as of May 2026.

## Platform posture

| Pillar | Status |
|---|---|
| **Scalable** | ECS Fargate primary, Railway warm failover, AWS Lambda for bursty batch jobs |
| **Secure** | API keys + SSO/OIDC, audit logging, OWASP Top-10 mitigations (IDOR, injection, crypto) |
| **Observable** | Prometheus + Grafana + Alertmanager, OpenTelemetry traces, synthetic monitoring |
| **Compliant** | SOC 2 ready — audit logs, access control, verified backups, IAM least-privilege |
| **Monetizable** | Ultra tier (R$ 2.900/mês) live with SSO + white-label + priority queue + dedicated support |

## Features

- **140,498 Brazilian towers** — ANATEL (105,240 across 12 operators and 5,570 municipalities, geocoded from IBGE municipality centroids) + OpenCelliD (35,248 GPS-tagged cells). A post-load pass (`snap_anatel.py`) snaps each ANATEL tower to the nearest same-operator OpenCelliD tower within 5 km when one is available.
- **Tiered pricing & billing** — 6 tiers (Free, Starter, Pro, Business, Enterprise, Ultra) + annual plans. Stripe webhook auto-provisions API keys on checkout success.
- **White-label tenant mode** — per-tenant branding, dynamic CORS, custom logo/favicon.
- **Audit log** — every tenant action recorded with actor, route, params, IP, and timestamp; queryable via `GET /tenant/audit`.
- **SSO / OIDC** — Cognito User Pool with Hosted UI; server-side OAuth code exchange; Bearer-token fallback in `verify_api_key` lets enterprise users hit the raw API directly with their Cognito ID token.
- **Priority batch queue** — Enterprise/Ultra traffic routed to a dedicated SQS queue with its own Lambda consumer, isolated from free/pro batch load.
- **Hop-viability Redis cache** — terrain LoS results memoized; `/plan_repeater` latency drops below 100 ms on warm cache.
- **Real-time AI coverage heatmap** — Server-Sent Events stream coverage tiles as they're computed; per-tier grid-resolution caps.
- **Geo exports** — KML, Shapefile, and GeoJSON downloads for any analyzed region.
- **Coverage model validation, public** — `GET /coverage/model/info` returns rmse_db (in-sample), cv_rmse_db (k-fold holdout), per-morphology RMSE (open_or_flat / rural_rolling / rural_mountainous) and per-band RMSE (700/850/900/1800/2100/2600/3500 MHz). All values exposed as Prometheus gauges. Methodology page: [Validação do modelo](model-validation.md).
- **Drive-test CSV importer** — `POST /coverage/observations/drivetest` ingests TEMS / G-NetTrack / QualiPoc / Anatel exports with auto-detection of column aliases (Latitude/lat, RSRP/RxLev/signal_dbm, Frequency [MHz]/band, etc.). Persisted with `source='drive_test'` so the daily retrain picks them up automatically.

## Operations

- **Synthetic monitoring** — GitHub Actions cron probes all three entrypoints (`api.*`, `app.*`, `docs.*`) every few minutes.
- **12 Prometheus alert rules** — covering 5xx rate, latency p95, queue depth, Lambda errors, ECS task health, certificate expiry, and disk pressure.
- **Multi-channel alerting** — Slack for warning/info, PagerDuty (Events API v2, `send_resolved=true`) for `severity=critical` only.
- **Alertmanager external URL** — `https://alerts.telecomtowerpower.com.br` (no more container-hostname links in alert payloads).

## Backups & DR

- **Nightly Postgres dump** to `s3://telecom-tower-power-results/backups/railway-postgres/` (14-day retention, gzipped, `set -euo pipefail` + size sanity check).
- **Nightly Grafana volume snapshot** to S3.
- **Weekly restore drill** (`backup-restore-drill.yml`) every Monday 07:15 UTC: pulls the latest dump, verifies it's ≤36 h old, restores into an ephemeral Postgres 18 container, and asserts row counts on `towers` (≥100k), `api_keys` (≥1), `alembic_version` (≥1). Failures optionally page via SNS.
- **Failover rotate & drift check** — periodic Route 53 / ALB drift detection between ECS and Railway.

## CI/CD — 19 hardened GitHub Actions workflows

| Workflow | Purpose |
|---|---|
| `deploy-ecs.yml` | Build + push to ECR, register new task def, force ECS service update with `deploymentCircuitBreaker={enable=true,rollback=true}` (auto-revert on failed health checks) |
| `deploy-ec2-docker.yml` | SSH via EC2 Instance Connect (ephemeral SG ingress) → capture prior commit SHA → `git pull` → on-host `docker compose build && up -d` → `/health` probe; auto `git reset --hard $PREV_SHA` + rebuild on failure |
| `deploy-lambda.yml` | SAM build/deploy for batch worker + priority worker + SSO callback |
| `deploy-caddy.yml` | scp Caddyfile → EC2, `caddy reload`, post-deploy health checks |
| `deploy-docs.yml` | Build mkdocs-material, push to S3, invalidate CloudFront |
| `sync-towers.yml` | Refresh ANATEL + OpenCelliD datasets into Postgres |
| `backup-railway-postgres.yml` | Nightly `pg_dump` → S3 (PGDG client 18, pipefail + size check) |
| `backup-grafana-volume.yml` | Nightly tar of `grafana_data` volume → S3 |
| `backup-restore-drill.yml` | **Weekly verified restore** into ephemeral Postgres 18 |
| `failover-rotate.yml` | Cycle Route 53 health-check failover |
| `failover-drift-check.yml` | Detect divergence between ECS and Railway |
| `synthetic-monitor.yml` | Black-box probes against `api.*`, `app.*`, `docs.*` |
| `update-ec2-stripe-secrets.yml` | SSM → EC2 secrets file, recreate stripe-webhook + api containers |
| `update-ec2-alerting-secrets.yml` | SSM → EC2 (PagerDuty routing key), recreate grafana + alertmanager, validate `pagerduty` config is loaded |
| `fix-alb-prometheus.yml` | Recover Prometheus target-group registration |
| `ec2-diagnose.yml` | One-shot SSM diagnostics dump |

All workflows: Node 24, `concurrency:` group, exponential retries on flaky AWS calls, `set -euo pipefail` in every multi-line script, no plaintext secrets in logs.

## Security

- API keys are SHA-256 hashed at rest; rotation supported via `/tenant/keys`.
- SSO ID tokens validated for `iss`, `aud`, `exp`, `sub`, `token_use=id`, with `RS256/RS384/RS512` accepted.
- OWASP Top-10 mitigations: parameterized SQL everywhere, IDOR-safe scoping by `tenant_id`, strict CSP + HSTS at Caddy, encrypted secrets via SSM SecureString + KMS, signed Stripe webhooks, rate limiting per tier.
- ALB host-header rule pins `api.*` to ECS only; frontend hosts (`app.*`, `www.*`, `docs.*`, apex) go to EC2 — no cross-pollination of routing.
