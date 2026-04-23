# Monitoring (Railway)

Prometheus + Grafana stack for deployment on **Railway**. Lives in the
existing Railway project `astonishing-harmony` / env `production` so the
monitoring services can reach the app services via Railway private DNS
(`*.railway.internal`).

> The EC2 docker-compose stack (`prometheus/`, `grafana/` at repo root)
> is kept intact as a backup. This folder is the Railway version.

## Layout

```
monitoring/
├── prometheus/
│   ├── Dockerfile            # prom/prometheus:v3.11.2
│   ├── prometheus.yml        # scrapes *.railway.internal targets
│   └── alert_rules.yml       # same rules as EC2
└── grafana/
    ├── Dockerfile            # grafana/grafana:12.2.0
    ├── provisioning/
    │   ├── datasources/      # Prometheus datasource (internal DNS)
    │   ├── dashboards/       # dashboard provider
    │   └── alerting/         # contact points, policies, rules
    └── dashboards/
        └── telecom.json
```

## Railway setup (one-time, do this in the dashboard)

### 1. Prometheus service

- **New service → Empty Service** in the `astonishing-harmony` project.
- **Name:** `prometheus` *(lowercase — the Grafana datasource points at
  `prometheus.railway.internal`)*.
- **Source:** connect to the `TELECOM-TOWER-POWER` GitHub repo.
- **Settings → Source:**
  - Root Directory: *(empty)*
  - Dockerfile Path: `monitoring/prometheus/Dockerfile`
- **Settings → Networking:** no public domain needed (scraped privately
  by Grafana). Optionally expose a domain for debugging.
- **Volumes:** mount `/prometheus` for time-series persistence.
  Default retention is 15 days; size to taste.
- **Env vars:** none required.

### 2. Grafana service

- **New service → Empty Service** in the same project.
- **Name:** `grafana`.
- **Source / Settings:**
  - Root Directory: *(empty)*
  - Dockerfile Path: `monitoring/grafana/Dockerfile`
- **Networking:** generate a public domain (`*.up.railway.app`). Set
  `GF_SERVER_ROOT_URL` to that URL.
- **Volumes:** mount `/var/lib/grafana` for dashboard state & users.
- **Env vars:**

  | Variable | Value |
  |---|---|
  | `GF_SECURITY_ADMIN_PASSWORD` | strong random secret |
  | `GF_SERVER_ROOT_URL` | `https://<generated>.up.railway.app` |
  | `SLACK_WEBHOOK_URL` | Slack incoming webhook (used by `contactpoints.yml`) |
  | `ALERT_EMAIL_TO` | `daniel.novais@sempreceub.com` *(default set in Dockerfile)* |
  | `SLACK_CHANNEL` | `#alerts` *(default set in Dockerfile)* |

## Gotchas

- **Same project required.** `*.railway.internal` DNS only resolves
  within a single Railway project. Do NOT put monitoring in a separate
  project, or change `prometheus.yml` + the Grafana datasource to
  public HTTPS URLs.
- **Service name = hostname.** Railway derives the internal DNS name
  from the service name. If you name the Prometheus service something
  other than `prometheus`, update:
  - `monitoring/grafana/provisioning/datasources/prometheus.yml`
- **Scrape ports.** Internal DNS uses the **container port the target
  service binds to**, not the public Railway port. Current assumptions:
  - `web.railway.internal:8000` (uvicorn default)
  - `worker.railway.internal:8080`
  - `redis-metrics-collector.railway.internal:9121`

  Verify these match the actual bind ports of those services. Uvicorn
  binds to `$PORT` which Railway sets per-service; if Railway overrides
  with a different value, update `prometheus.yml` and redeploy.
- **Alertmanager removed.** Alert delivery is handled entirely by
  Grafana's provisioned contact points + notification policies (Slack +
  email via SES). The `alerting.alertmanagers` block in `prometheus.yml`
  is intentionally empty.
- **Dashboards volume mask.** The provisioning path is
  `/etc/grafana/dashboards/` (not `/var/lib/grafana/dashboards/`) so the
  named volume on `/var/lib/grafana` doesn't mask the baked-in dashboard
  JSON. Same fix as the EC2 setup (see repo notes on grafana 12.0.2).

## Post-deploy validation

```bash
# From your laptop, against the public Grafana domain:
curl https://<grafana>.up.railway.app/api/health
# Expect: {"database":"ok","version":"12.2.0"}

# Inside Grafana UI:
# - Configuration → Data sources → Prometheus → Save & test → green check
# - Explore → run `up` → one series per scrape job, value=1
# - Dashboards → Telecom Tower Power → panels render data
# - Alerting → Contact points → Test → Slack + email deliver
```
