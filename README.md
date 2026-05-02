# TELECOM TOWER POWER

**Production B2B SaaS for telecom RF engineering — tailored to rural Brazil.**

A single tiered API and managed database that gives RF engineers everything needed to design, validate and document long-range cellular and point-to-point links over Brazilian terrain:

1. **Tower database management** — 140,498 georeferenced towers (ANATEL + OpenCelliD), CRUD + nearest-neighbour search.
2. **Point-to-point link analysis** — Fresnel zone clearance, line-of-sight, RSSI, SRTM terrain profile (`POST /analyze`).
3. **Multi-hop repeater planning** — Dijkstra path search over candidate towers with terrain-aware edge costs (`POST /plan_repeater[/async]`).
4. **Batch PDF report generation** — sync ZIP for ≤100 receivers, async SQS+Lambda priority queue for Enterprise (`POST /batch_reports`, `GET /jobs/{id}`).
5. **AI-assisted analysis** — Amazon Bedrock chat + scenario comparison (`POST /bedrock/chat`, `/bedrock/compare`), with playground SSE streaming.
6. **Terrain-aware ML signal prediction** — ridge-regression model (17 features incl. SRTM elevation profile, fresnel ratio, terrain roughness) outperforms physics-only Hata baseline; nightly retrain in CI, S3-hot-pulled at boot (`POST /coverage/predict`, `GET /coverage/model/info`).

> **140,498 towers** across Brazil — 105,240 from ANATEL (12 operators, 5,570 municipalities) + OpenCelliD crowd-sourced data. Default tower parameters: 35 m height, 43 dBm power, 700/1800 MHz bands.

![Python 3.10](https://img.shields.io/badge/python-3.10-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688)
![License](https://img.shields.io/badge/license-Commercial-red)

---

## Production Status

- **Infrastructure** — ECS Fargate primary (`api.*`), EC2 + Docker Compose for frontend/Grafana/Prometheus/Alertmanager (`app.*`, `www.*`, `monitoring.*`, `prometheus.*`), Railway as warm failover, Lambda for bursty batch jobs (priority queue for Enterprise).
- **CI/CD** — **16** hardened GitHub Actions workflows under [.github/workflows](.github/workflows): deploys (ECS, EC2 Docker, Lambda, Caddy, docs), secret-sync (Stripe + PagerDuty via SSM), nightly Postgres + Grafana backups, **weekly restore drill** (`backup-restore-drill.yml`), failover rotate/drift, synthetic monitoring, and EC2 diagnostics. All Node 24, with `concurrency:` control, retries, and BuildKit cache.
- **Observability** — Prometheus + Grafana + Alertmanager + OpenTelemetry traces; **12** Prometheus alert rules; Slack + PagerDuty (critical-only, `send_resolved=true`); synthetic monitoring probes all three entrypoints; Alertmanager external URL `https://alerts.telecomtowerpower.com.br`.
- **Backups** — Grafana volume + PostgreSQL nightly to S3 (14-day retention); **verified restore** every Monday 07:15 UTC via ephemeral Postgres 18 container + row-count assertions on `towers`, `api_keys`, `alembic_version`.
- **Security** — API keys + SSO/OIDC (Cognito, Bearer fallback in `verify_api_key`), audit log on every tenant action, OWASP-Top-10 mitigations (IDOR, injection, crypto), TLS at ALB + Caddy.
- **Secrets** — Never committed; synced via GitHub Actions → SSM Parameter Store → `/home/ubuntu/TELECOM-TOWER-POWER/secrets/` and ECS task-def secret refs. See [secrets/README.md](secrets/README.md).

See [docs-site/docs/operations/production-status.md](docs-site/docs/operations/production-status.md), [docs-site/docs/operations/runbook.md](docs-site/docs/operations/runbook.md), and [docs-site/docs/operations/whats-shipped.md](docs-site/docs/operations/whats-shipped.md).

---

## What's Shipped (Cumulative)

All features and security items shipped to production are live, tested, and documented:

### Core RF engineering capabilities (all 6 verified live in prod 2026-04-29)

| # | Capability | Endpoint(s) | Status |
|---|---|---|---|
| 1 | Tower database management | `GET/POST /towers`, `/towers/nearest`, `/towers/{id}` | ✅ 140,498 towers, PostgreSQL |
| 2 | Point-to-point link analysis | `POST /analyze` | ✅ SRTM terrain, Fresnel/LOS/RSSI, capped knife-edge penalty |
| 3 | Multi-hop repeater planning | `POST /plan_repeater[/async]` | ✅ Dijkstra + Redis hop cache, async for max_hops≥4 |
| 4 | Batch PDF reports | `POST /batch_reports`, `/jobs/{id}` | ✅ Sync ZIP ≤100 rows, async SQS+Lambda for Enterprise |
| 5 | AI-assisted analysis (Bedrock) | `POST /bedrock/chat`, `/bedrock/compare`, `/bedrock/models` | ✅ Foundation model catalog, scenario compare |
| 6 | Terrain-aware ML signal prediction | `POST /coverage/predict`, `GET /coverage/model/info` | ✅ ridge-v1 (rmse 12.94 dB, n=20000, 17 features), S3 hot-pull, nightly retrain |

### Platform & ops

- **Tiered pricing & billing** — 5 tiers + annual; Stripe webhook auto-provisions API keys
- **White-label tenant mode** — branding + dynamic CORS
- **Audit log** — every tenant action recorded, queryable via `/tenant/audit`
- **SSO / OIDC** — Cognito Hosted UI + server-side OAuth code exchange; Bearer token fallback in `verify_api_key`
- **Priority batch queue** — Enterprise SQS + dedicated Lambda consumer
- **Hop-viability Redis cache** — drops `/plan_repeater` latency to <100 ms
- **Real-time AI coverage heatmap** — SSE, per-tier grid caps
- **KML / Shapefile / GeoJSON export**
- **Synthetic monitoring** — GitHub Actions cron probes all three entrypoints
- **12 Prometheus alert rules** + Slack + PagerDuty (critical only)
- **Backups** — Grafana volume + PostgreSQL nightly to S3 with **verified restore** drill
- **16 hardened GitHub Actions workflows** — concurrency, retries, SSM secrets sync

---

## Architecture

For a deeper dive (sequence diagrams, ML pipeline, request lifecycle) see
[docs-site/docs/operations/architecture.md](docs-site/docs/operations/architecture.md)
(rendered at `https://docs.telecomtowerpower.com.br/operations/architecture/`).

```
                    ┌──────────────────────────── PUBLIC INGRESS ────────────────────────────┐
                    │                                                                         │
   api.telecomtowerpower.com.br ────────► AWS ALB (sa-east-1) ────────► ECS Fargate           │
   app/www/monitoring/prometheus.* ─────► Caddy on EC2 (EIP 18.229.14.122)                    │
   web-production-90b1f.up.railway.app ─► Railway router (warm failover)                      │
                    └─────────────────────────────────────────────────────────────────────────┘
                                                  │
        ┌─────────────────────────────────────────┼────────────────────────────────────────┐
        ▼                                         ▼                                        ▼
┌──────────────────┐               ┌──────────────────────────────┐            ┌──────────────────┐
│  ECS Fargate     │               │  EC2  (Docker Compose stack) │            │  Railway service │
│  rev 38+         │               │  api · frontend · streamlit  │            │  identical image │
│  prod-of-record  │               │  prometheus · grafana · loki │            │                  │
└────────┬─────────┘               └─────────┬────────────────────┘            └──────┬───────────┘
         │                                   │                                         │
         └───────────────────┬───────────────┴─────────────────────────────────────────┘
                             ▼
              ┌──────────────────────────────┐
              │   FastAPI app  (3700+ LOC)   │      telecom_tower_power_api.py
              │   Auth · Rate-limit · Audit  │      verify_api_key · require_tier
              │   CORS · Prometheus metrics  │
              │                              │
              │   /towers · /towers/nearest  │      Tower DB (CRUD + nearest-neighbour)
              │   /analyze                   │      Fresnel · LOS · RSSI · SRTM profile
              │   /plan_repeater[/async]     │      Dijkstra · Redis hop cache
              │   /coverage/predict          │      ridge-v1 ML  (terrain-aware)
              │   /coverage/observations     │      Real-RSSI ingestion (DB-backed retrain)
              │   /batch_reports · /jobs/{id}│      Sync ZIP · async SQS+Lambda
              │   /bedrock/{chat,compare}    │      AWS Bedrock foundation models
              │   /tenant/* · /admin/*       │      Branding · audit · sales · impersonate
              └─────────────┬────────────────┘
                            │
       ┌────────────────────┼─────────────────────┬──────────────────────────────┐
       ▼                    ▼                     ▼                              ▼
┌───────────────┐ ┌──────────────────┐ ┌───────────────────────┐ ┌────────────────────────┐
│ PostgreSQL 18.3│ │ Redis 8.6.2      │ │ S3                    │ │ External APIs          │
│ Railway prod /│ │  hop cache       │ │  models/coverage_*.npz│ │  AWS Bedrock           │
│ SQLite dev    │ │  jobs queue      │ │  reports/{tenant}/*   │ │  Stripe (billing)      │
│ towers·jobs   │ │  rate-limits     │ │  backups/postgres/*   │ │  Cognito (OIDC SSO)    │
│ api_keys·audit│ │                  │ │  backups/grafana/*    │ │  NASA SRTM             │
│ link_obs·cells│ │                  │ │                       │ │  OpenCelliD / ANATEL   │
└────────────────┘ └──────────────────┘ └─────────────────────┘ └──────────────────────┘
                                                  ▲
                                                  │ refresh_from_s3() on container boot
                                                  │
                  ┌───────────────────────────────┴──────────────────────────────┐
                  │  ML pipeline (coverage_predict.py)                            │
                  │   Nightly CI ─► retrain (synthetic baseline + real             │
                  │                  link_observations from POST /coverage/        │
                  │                  observations or scripts/seed_observations.py  │
                  │                  bulk-load once ≥1k rows accumulate)            │
                  │             ─► coverage_model.npz  (ridge-v1, 17 features)    │
                  │             ─► aws s3 cp s3://.../models/                     │
                  │   Boot      ─► entrypoint.sh refresh_from_s3 ─► load → log    │
                  │             "Coverage model active: ver=ridge-v1 rmse=12.94"  │
                  └───────────────────────────────────────────────────────────────┘

SQLite fallback: when DATABASE_URL is not set, the API and worker use a local
towers.db file automatically — no PostgreSQL required for development.
```

| Component | File(s) | Purpose |
|---|---|---|
| **API** | `telecom_tower_power_api.py` | FastAPI backend — all endpoints, auth, rate limiting |
| **Database layer** | `tower_db.py` | Dual SQLite/PostgreSQL persistence (auto-detected) |
| **Job queue** | `job_store.py` | Persistent batch job queue (DB-backed) |
| **Batch worker (EC2/ECS)** | `batch_worker.py` | Background process — polls jobs, generates PDF ZIPs |
| **Batch worker (Lambda)** | `sqs_lambda_worker.py` | SQS-triggered Lambda — serverless batch processing |
| **S3 storage** | `s3_storage.py` | Report upload/download with presigned URLs (3 600 s expiry) |
| **DB migration** | `migrate_csv_to_db.py` | CLI to load tower CSV into the database |
| **Schema versioning** | `alembic.ini`, `migrations/` | Alembic database migrations |
| **Standalone engine** | `telecom_tower_power.py` | Sync library (no server dependency) |
| **Elevation** | `srtm_elevation.py` | SRTM3 .hgt reader — bilinear interp, Redis L2 cache |
| **Elevation prefetch** | `srtm_prefetch.py` | Pre-download SRTM tiles per country bounding box |
| **PDF reports** | `pdf_generator.py` | A4 engineering reports with terrain/Fresnel charts |
| **Billing** | `stripe_billing.py` | Stripe Checkout, webhook handling, key lifecycle |
| **React UI** | `frontend/src/` | Leaflet map, link analysis, repeater planner |
| **Streamlit UI** | `frontend.py`, `streamlit_app.py` | Dashboard with Folium maps, batch job tracking |
| **Tower loader** | `load_towers.py` | Bulk CSV → API ingestion script |
| **Monitoring** | `grafana_dashboard.json`, `prometheus.yml` | Pre-built Grafana dashboard + Prometheus config |

---

## Quick Start

### Option A: Docker Compose (recommended)

Brings up the full stack — PostgreSQL, API, worker, frontend, Prometheus, and Grafana:

```bash
docker-compose up
```

| Service | URL | Purpose |
|---|---|---|
| API | http://localhost:8000 | FastAPI + Swagger docs at `/docs` |
| Streamlit | http://localhost:8501 | Streamlit dashboard |
| Prometheus | http://localhost:9090 | Metrics scraper |
| Grafana | http://localhost:3001 | Dashboards (login: `admin`/`admin`) |
| PostgreSQL | localhost:5432 | Database (user: `telecom`, db: `towers`) |

The `load-towers` service automatically seeds the database from `towers_brazil.csv` on first run.

### Option B: Local development (SQLite)

No Docker or PostgreSQL required — uses SQLite automatically.

#### 1. Install dependencies

```bash
pip install -r requirements.txt
```

#### 2. Seed the database

```bash
python migrate_csv_to_db.py --csv towers_brazil.csv --clear
```

This creates `towers.db` with all towers from the CSV.

#### 3. Start the API server

```bash
uvicorn telecom_tower_power_api:app --host 127.0.0.1 --port 8000
```

#### 4. Start the background worker (separate terminal)

```bash
python batch_worker.py --poll-interval 2
```

The worker processes batch PDF jobs queued via `POST /batch_reports`.

#### 5. Launch the Streamlit frontend (optional)

```bash
streamlit run frontend.py
```

#### 6. Launch the React frontend (optional)

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000, proxies /api → backend
```

### Database setup

The platform auto-detects the database backend:

| `DATABASE_URL` env var | Backend | Use case |
|---|---|---|
| Not set | **SQLite** (`towers.db`) | Local dev, single-instance |
| `postgresql://...` | **PostgreSQL** | Production, multi-worker |

To switch to PostgreSQL locally:

```bash
export DATABASE_URL=postgresql://telecom:telecom_secret@localhost:5432/towers
alembic upgrade head                                  # apply schema migrations
python migrate_csv_to_db.py --csv towers_brazil.csv --clear
uvicorn telecom_tower_power_api:app --host 0.0.0.0 --port 8000
```

### Schema migrations (Alembic)

Database schema is version-controlled with [Alembic](https://alembic.sqlalchemy.org/).

```bash
# Apply all pending migrations
alembic upgrade head

# Check current revision
alembic current

# Create a new migration after changing models.py
alembic revision --autogenerate -m "describe_change"

# Downgrade one revision
alembic downgrade -1
```

In Docker Compose, the `migrate` service runs `alembic upgrade head` automatically before the API starts.

### Prometheus / Grafana quick start

With Docker Compose, Prometheus and Grafana start automatically. To connect them:

1. Open Grafana at http://localhost:3001 (login: `admin` / `admin`)
2. Add data source → Prometheus → URL: `http://prometheus:9090`
3. Import dashboard → Upload `grafana_dashboard.json` (or it's auto-mounted)

Without Docker, scrape the API's `/metrics` endpoint with any Prometheus instance:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: "telecom-tower-power-api"
    static_configs:
      - targets: ["localhost:8000"]
```

---

## API Reference

Base URL: `http://localhost:8000` · Full docs at `/docs` (Swagger UI)

### Authentication

All endpoints (except `GET /`, `GET /health`, `GET /metrics`, and signup routes) require:

```
X-API-Key: <your-key>
```

**Demo keys** (rate-limited to 10 rpm, no PDF, no AI; rotated monthly):

| Key | Tier |
|---|---|
| `demo_ttp_free_2604` | Free |
| `demo_ttp_starter_2604` | Starter |
| `demo_ttp_pro_2604` | Pro |

### Tier Limits

| | Free | Pro | Enterprise |
|---|---|---|---|
| **Price** | $0 | R$ 1.000/mês (~$200) | R$ 5.000/mês (~$1 000) |
| Requests/min | 10 | 100 | 1,000 |
| Max towers (per key) | 20 | 500 | 10,000 |
| PDF export | — | ✓ | ✓ |
| Batch rows | — | 2,000 | 10,000 |
| AI assistant | — | ✓ | ✓ |

Tower creation is **rate-limited per API key** — an in-memory counter tracks towers created per key and returns `403` when the tier limit is reached.

### Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Service info |
| `GET` | `/health` | — | Liveness/readiness probe |
| `GET` | `/metrics` | — | Prometheus metrics (text/plain) |
| `POST` | `/towers` | Any tier | Register a tower |
| `GET` | `/towers` | Any tier | List towers (filter by `operator`, `limit`) |
| `GET` | `/towers/nearest` | Any tier | Find nearest towers to a coordinate |
| `POST` | `/analyze` | Any tier | Run link analysis (FSPL, Fresnel, LOS, terrain) |
| `POST` | `/plan_repeater` | Any tier | Dijkstra-optimized multi-hop repeater chain |
| `POST` | `/coverage/predict` | Pro+ | ML-based signal prediction (point or grid) — terrain-aware, SageMaker-backed |
| `POST` | `/coverage/observations` | Any tier | Submit a real RSSI measurement to improve the model |
| `POST` | `/coverage/observations/batch` | Pro+ | Bulk-upload up to 10 000 measurements |
| `POST` | `/coverage/observations/drivetest` | Pro+ | TEMS / G-NetTrack / QualiPoc CSV importer (auto-detected columns) |
| `GET` | `/coverage/observations/stats` | Any tier | Counts of stored observations (link_observations) |
| `GET` | `/coverage/model/info` | Any tier | Model metadata: rmse, k-fold cv_rmse, per-morphology + per-band RMSE |
| `GET` | `/export_report` | Pro+ | Download PDF link report |
| `GET` | `/export_report/pdf` | Pro+ | Download PDF link report (alias) |
| `POST` | `/batch_reports` | Pro+ | Upload CSV → ZIP of PDFs (≤100 sync, >100 async job) |
| `GET` | `/jobs/{job_id}` | Pro+ | Poll background batch job status |
| `GET` | `/jobs/{job_id}/download` | Pro+ | Download completed batch job ZIP |
| `POST` | `/signup/free` | — | Self-service free-tier signup |
| `POST` | `/signup/checkout` | — | Create Stripe Checkout session (Pro/Enterprise) |
| `GET` | `/signup/success` | — | Retrieve API key after Stripe payment |
| `POST` | `/signup/status` | — | Look up existing key by email |
| `POST` | `/stripe/webhook` | Stripe sig | Stripe event handler |
| `POST` | `/bedrock/chat` | Pro+ | AI-powered RF engineering assistant |
| `POST` | `/bedrock/compare` | Pro+ | AI comparison of multiple link analyses |
| `POST` | `/bedrock/suggest-height` | Pro+ | AI-optimized antenna height recommendation |
| `GET` | `/bedrock/models` | Pro+ | List available Bedrock AI models |
| `POST` | `/bedrock/batch-analyze` | Enterprise | AI batch analysis of multiple links |
| `POST` | `/srtm/prefetch` | Pro+ | Pre-download SRTM elevation tiles for a country |
| `GET` | `/srtm/status/{country}` | Pro+ | Check SRTM tile download status |
| `GET` | `/portal/profile` | Any tier | Account profile and tier info |
| `GET` | `/portal/usage` | Any tier | API usage statistics |
| `GET` | `/portal/jobs` | Any tier | List batch jobs |
| `GET` | `/portal/billing` | Any tier | Billing/subscription info |

### Example: Analyze a link

```bash
# Register a tower
curl -X POST http://localhost:8000/towers \
  -H "X-API-Key: demo_ttp_pro_2604" \
  -H "Content-Type: application/json" \
  -d '{"id":"T1","lat":-15.78,"lon":-47.93,"height_m":45,"operator":"Vivo","bands":["700MHz"],"power_dbm":46}'

# Analyze link to a receiver
curl -X POST "http://localhost:8000/analyze?tower_id=T1" \
  -H "X-API-Key: demo_ttp_pro_2604" \
  -H "Content-Type: application/json" \
  -d '{"lat":-15.85,"lon":-47.81,"height_m":10,"antenna_gain_dbi":12}'
```

### Example: Batch PDF reports

```bash
curl -X POST "http://localhost:8000/batch_reports?tower_id=VIVO_001" \
  -H "X-API-Key: demo_ttp_pro_2604" \
  -F "csv_file=@sample_receivers.csv" \
  -o reports.zip
```

### Example: Self-service signup

```bash
# Free tier — instant API key
curl -X POST http://localhost:8000/signup/free \
  -H "Content-Type: application/json" \
  -d '{"email":"engineer@company.com"}'

# Paid tier — redirects to Stripe Checkout
curl -X POST http://localhost:8000/signup/checkout \
  -H "Content-Type: application/json" \
  -d '{"email":"engineer@company.com","tier":"pro"}'
```

---

## Engineering Details

### RF Propagation Model

- **Free-Space Path Loss (FSPL)** at 700 / 1800 / 2600 / 3500 MHz
- **Earth curvature correction (k = 4/3)** — effective Earth radius $R_{\text{eff}} = 6371 \times 1.33$ km; earth-bulge subtracted at each profile sample point
- **Fresnel zone clearance** — 1st Fresnel radius computed per-link with earth-bulge correction for long-distance paths
- **Terrain-aware LOS** — 30-point elevation profile (SRTM3 + Open-Elevation API fallback)
- **Link budget** — TX power, antenna gains, cable losses, fade margin

### Multi-Hop Repeater Planning

The `plan_repeater` endpoint uses a **terrain-aware Dijkstra** algorithm:

1. Generate candidate repeater sites from existing towers
2. Pre-compute hop costs: FSPL + obstruction penalty (up to ~20 dB for Fresnel clearance < 0.6)
3. Find the optimal bottleneck path minimizing worst-case hop cost
4. Respect configurable `max_hops` constraint (default 3)

### Elevation Data

Four-layer resolution with automatic failover:

| Priority | Source | Resolution | Latency |
|---|---|---|---|
| 1 | In-memory tile cache (dict) | exact | ~0 ms |
| 2 | Redis L2 cache (`SRTM_REDIS_URL`) | exact | ~1 ms |
| 3 | SRTM `.hgt` tiles on disk | ~90 m | ~2 ms |
| 4 | Open-Elevation API | variable | ~200 ms |

Redis stores raw tile blobs (key `srtm:{tile}`, 7-day TTL). In Docker Compose, Redis runs with `--maxmemory 256mb --maxmemory-policy allkeys-lru`.

Place `.hgt` files in `./srtm_data/` (or set `SRTM_DATA_DIR`). Use `srtm_prefetch.py` to pre-download all tiles for a country:

```bash
python srtm_prefetch.py --country BR    # downloads ~240 tiles for Brazil
```

### Serverless Batch Pipeline (SQS + Lambda)

The platform uses **hybrid batch processing**:

| Batch size | Processing mode | Delivery |
|---|---|---|
| ≤ 100 rows | **Synchronous** — API generates ZIP inline | Direct HTTP response |
| > 100 rows | **Asynchronous** — SQS → Lambda worker → S3 | Presigned URL (1 h expiry) |

```
Client ─▸ POST /batch_reports ─▸ API
                                 ├── ≤100 rows → generate ZIP → 200 response
                                 └── >100 rows → persist job → SQS message
                                                                   │
                                     S3 ◂── ZIP ◂── Lambda worker ◂┘
                                      │
                  GET /jobs/{id}/download ◂── presigned URL ◂── S3
```

Resources (SAM `template.yaml`):
- **SQS queue** `telecom-batch-jobs-{stage}` with DLQ (max 3 retries)
- **Lambda** `sqs_lambda_worker.handler` (1 024 MB, 900 s timeout)
- **S3 bucket** for generated report ZIPs

### PDF Reports

Generated with ReportLab + Matplotlib:
- Tower & receiver info tables
- Full link budget breakdown
- Feasibility assessment & recommendation
- Terrain profile with Fresnel zone visualization

---

## Monitoring

`GET /metrics` exposes Prometheus-compatible metrics. All API request logs are structured JSON.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `http_request_duration_seconds` | Histogram | `method`, `endpoint`, `status`, `tier` | Request latency (10 buckets: 10 ms – 10 s) |
| `http_requests_total` | Counter | `method`, `endpoint`, `status`, `tier` | Total request count |
| `rate_limit_hits_total` | Counter | `tier` | Rate-limit 429 rejections |
| `batch_jobs_active` | Gauge | — | Background batch jobs currently running |
| `batch_jobs_duration_seconds` | Histogram | — | Time to process a batch job |

**Response headers** on authenticated requests:
- `X-RateLimit-Remaining` — calls left in the current minute
- `X-RateLimit-Limit` — total calls allowed per minute for the tier

**Structured JSON logs** — every request emits:
```json
{"timestamp": "...", "level": "INFO", "message": "request",
 "http_method": "GET", "path": "/towers", "status": 200,
 "duration_ms": 15.0, "api_key_tier": "free"}
```

**Pre-built Grafana dashboard** (`grafana_dashboard.json`) includes:
- Request rate by endpoint and tier
- Latency percentiles (p50 / p90 / p99)
- Error rate (4xx / 5xx)
- Rate-limit hit rate by tier
- Active batch jobs gauge
- Batch job duration percentiles
- Coverage model: predicted vs measured RSSI (p50 / p90) per observation source
- Coverage model residual (predicted − measured) distribution
- Coverage observations ingested (per source)

### Alerting

**Alertmanager** (`alertmanager.yml`) is configured with three receivers:

| Receiver | Channel | Active |
|---|---|---|
| `default` | Slack `#alerts` | ✅ Active |
| `critical` | Slack `#alerts-critical` + SES email | ✅ Active |
| `warning` | Slack `#alerts-warning` | ✅ Active |

> **Slack** is fully operational — `SLACK_WEBHOOK_URL` is configured in SSM, ECS task definition, and Railway env vars. The API also sends fire-and-forget Slack alerts on 5xx errors and startup via `_alert_slack()`.

**Prometheus alert rules** (`prometheus_alert_rules.yml`) cover: high error rate, high latency, instance down, disk usage.

### Route 53 DNS Failover

Automatic DNS failover is **deployed and active**:

1. **Health check** (`f32babca-ad29-4d2c-9593-a455d11e5ab7`) — HTTPS string-match on ALB `/health`, checks for `"healthy"`, all 10 AWS regions reporting healthy
2. **Failover CNAME records** for `api.telecomtowerpower.com.br`:
   - **PRIMARY** → ALB (health-checked, TTL 60 s)
   - **SECONDARY** → Railway edge (`web-production-90b1f.up.railway.app`, TTL 60 s) — unique per custom domain; Railway issues a Let's Encrypt cert for `api.telecomtowerpower.com.br` and also requires a TXT `_railway-verify.api` record for ownership validation.

> **⚠ Operational notes for this failover path**
> - The Route 53 health check only measures the **ALB**. When the ALB fails the check, Route 53 will flip to the Railway secondary even if Railway itself is unhealthy or TLS validation is broken. Treat `scripts/verify_failover.sh` as the secondary-viability check (edge resolves, serves a cert covering `api.*`, TXT ownership record present).
> - `web-production-90b1f.up.railway.app` is an **internal implementation detail**. Do not point monitors, tests, SDKs, or integrations at it directly — always use `https://api.telecomtowerpower.com.br`. Railway may rotate the edge per custom domain at any time; rediscover it via the Railway UI and re-run `RAILWAY_DNS=<new>.up.railway.app scripts/setup_failover.sh`.
> - The `_railway-verify.api.telecomtowerpower.com.br` TXT record is a single point of failure for the SECONDARY leg. Any DNS automation that prunes "unknown" records must allowlist it.

If the ALB health check fails, Route 53 automatically routes traffic to Railway within ~60 s.

### Caddy Reverse Proxy (EC2)

The ALB terminates TLS and forwards HTTP to EC2. Two monitoring subdomains bypass Caddy via dedicated ALB target groups; everything else hits Caddy on port 80:

**ALB direct target groups (bypass Caddy):**

| Subdomain | ALB Target Group | Port | Health Check |
|---|---|---|---|
| `monitoring.telecomtowerpower.com.br` | `ttp-grafana-tg` | 3001 | `/api/health` |
| `prometheus.telecomtowerpower.com.br` | `ttp-prometheus-tg` | 9090 | `/-/healthy` |

**Caddy routes (ALB default rule → port 80):**

| Host header | Routing | Target |
|---|---|---|
| `api.telecomtowerpower.com.br` | **All paths** → Railway | `https://web-production-90b1f.up.railway.app` |
| `www.*` / `app.*` | API paths (`/api/*`, `/analyze`, `/health`, etc.) → Railway | `https://web-production-90b1f.up.railway.app` |
| `www.*` / `app.*` | `/webhook*` → local Stripe handler | `localhost:8001` |
| `www.*` / `app.*` | `/grafana*` → Grafana | `localhost:3001` |
| `www.*` / `app.*` | Everything else → React SPA | `localhost:3000` (nginx) |

The Caddyfile uses a `host` matcher to identify `api.*` traffic (which arrives via ALB after failover) and proxies **all** requests to Railway — no path whitelist needed. For `www.*`/`app.*`, only known API paths are forwarded; everything else serves the React SPA.

**Deployment:** The `deploy-caddy.yml` GitHub Actions workflow uses `scp` to copy the Caddyfile directly to EC2, then runs `caddy reload`. It verifies health on `www.*`, `app.*`, `api.*`, `monitoring.*`, and `prometheus.*` subdomains after deploy.

### Route 53 DNS Records

| Subdomain | Type | Target | Purpose |
|---|---|---|---|
| `app.telecomtowerpower.com.br` | A (alias) | ALB | React frontend (canonical) |
| `www.telecomtowerpower.com.br` | A (alias) | ALB | React frontend |
| `api.telecomtowerpower.com.br` | CNAME (failover) | ALB / Railway | API backend |
| `monitoring.telecomtowerpower.com.br` | A (alias) | ALB | Grafana dashboard |
| `prometheus.telecomtowerpower.com.br` | A (alias) | ALB | Prometheus |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(none → SQLite)* | PostgreSQL connection string; omit for local SQLite |
| `SRTM_DATA_DIR` | `./srtm_data` | Path to SRTM `.hgt` tile directory |
| `SRTM_REDIS_URL` | *(none)* | Redis URL for L2 SRTM tile cache (e.g. `redis://redis:6379/0`) |
| `CORS_ORIGINS` | `https://app.telecomtowerpower.com.br` | Comma-separated allowed CORS origins |
| `MAX_UPLOAD_BYTES` | `10485760` (10 MB) | Maximum request body size |
| `MAX_BATCH_ROWS` | `100` | Maximum rows per batch CSV upload |
| `RATE_LIMIT_FREE` | `10` | Requests/min for free tier |
| `RATE_LIMIT_PRO` | `100` | Requests/min for pro tier |
| `RATE_LIMIT_ENTERPRISE` | `1000` | Requests/min for enterprise tier |
| `VALID_API_KEYS` | *(demo keys)* | JSON dict `{"key":"tier"}` — overrides built-in demo keys |
| `STRIPE_SECRET_KEY` | — | Stripe API key (`sk_test_...` or `sk_live_...`) |
| `STRIPE_WEBHOOK_SECRET` | — | Stripe webhook signing secret (`whsec_...`) |
| `STRIPE_PRICE_PRO` | — | Stripe Price ID for Pro plan |
| `STRIPE_PRICE_ENTERPRISE` | — | Stripe Price ID for Enterprise plan |
| `FRONTEND_URL` | `http://localhost:3000` | Checkout redirect base URL |
| `KEY_STORE_PATH` | `./key_store.json` | Persistent API key store path |
| `PORT` | `8000` | Server port (used by Dockerfile/Procfile) |
| `BEDROCK_REGION` | `us-east-1` | AWS region for Amazon Bedrock invocations |
| `BEDROCK_MODEL_ID` | `amazon.nova-micro-v1:0` | Foundation model used by `/bedrock/*` and `/coverage/predict?explain=true` |
| `COVERAGE_MODEL_PATH` | `coverage_model.npz` | Path to the local ridge-regression model artefact (`python -m coverage_predict train`) |
| `COVERAGE_MODEL_S3_URI` | *(none)* | Optional `s3://bucket/key` fallback — downloaded once on first request when the local file is missing (no rebuild required for model updates) |
| `COVERAGE_BAND_MODEL_DIR` | *(none)* | Local directory holding per-band ridge artefacts (`coverage_model_<MHz>.npz` + `manifest.json`). When populated, `/coverage/predict` automatically dispatches to the closest-band ridge and falls back to the global model |
| `COVERAGE_BAND_MODELS_S3_PREFIX` | *(none)* | Optional `s3://bucket/prefix` to hydrate `COVERAGE_BAND_MODEL_DIR` on container boot (entrypoint syncs whitelisted files only) |
| `MAPBIOMAS_RASTER_PATH` | *(none)* | Path to a MapBiomas LULC GeoTIFF (Collection 9, single-band uint8). When set + readable + `rasterio` installed, `/coverage/predict` (point mode) returns `clutter_class` + `clutter_label` at the rx coordinate. Lookups are cached in Redis (`MAPBIOMAS_REDIS_URL` → falls back to `REDIS_URL` → in-memory LRU) |
| `MAPBIOMAS_REDIS_URL` | *(falls back to `REDIS_URL`)* | Override Redis endpoint for the clutter cache. Set to empty string to disable Redis (LRU only) |
| `MAPBIOMAS_REDIS_TTL_S` | `2592000` (30d) | TTL for cached clutter lookups |
| `SAGEMAKER_COVERAGE_ENDPOINT` | *(none → local model)* | Real-time SageMaker endpoint name; when set, `/coverage/predict` routes to it. Falls back to local model and finally physics if absent / unreachable |
| `SAGEMAKER_REGION` | `$AWS_REGION` or `us-east-1` | AWS region of the SageMaker endpoint |

---

## Deployment

### Docker

```bash
docker build -t telecom-tower-power .
docker run -p 8000:8000 -v ./srtm_data:/app/srtm_data:ro telecom-tower-power
```

### Docker Compose (full stack)

```bash
docker-compose up
```

Starts nine services:
- **postgres** — PostgreSQL 16 database
- **pgbouncer** — PgBouncer 1.23 connection pooler (transaction mode, pool 20, max 200, port 6432)
- **redis** — Redis 7 for SRTM tile L2 cache (256 MB, LRU eviction)
- **api** — FastAPI on port 8000 with healthcheck
- **worker** — Background batch job processor
- **frontend** — Streamlit on port 8501
- **load-towers** — one-shot CSV → DB seeder
- **prometheus** — Metrics scraper on port 9090
- **grafana** — Dashboards on port 3001

### AWS SAM (Lambda + API Gateway)

Serverless deployment via SAM (`template.yaml`):

```bash
# Build (requires Docker for python3.12 runtime)
sam build --use-container --build-dir /mnt/sam-workspace/build

# Deploy
sam deploy --template-file /mnt/sam-workspace/build/template.yaml \
  --stack-name telecom-tower-power-prod --region sa-east-1 \
  --resolve-s3 --capabilities CAPABILITY_IAM \
  --parameter-overrides "Stage=prod DatabaseUrl=postgresql+asyncpg://..."
```

Creates: API Gateway (HTTP API) → Lambda (FastAPI via Mangum), SQS queue + DLQ, batch-worker Lambda, S3 reports bucket, IAM roles.

The build uses `BuildMethod: makefile` to precisely control which files and dependencies are packaged (183 MB unzipped, 54 MB zipped — well under the 262 MB Lambda limit). `boto3`/`botocore` are excluded (already in the Lambda runtime).

### AWS ECS (Fargate)

Full ECS deployment with ALB:

- **ECS task** (`ecs-task-definition.json`) — API + worker containers
- **EFS volumes** — shared `srtm-data` and `job-results` across tasks (transit encryption enabled)
- **ALB** with target group `telecom-tower-power-api-tg` on port 8000
- **EC2 manage** (`scripts/manage_ec2_alb.sh`) — register/deregister EC2 from ALB for cold standby

> **Operational lesson — stale ALB targets:** If an EC2 instance is terminated without deregistering from the ALB target group, the orphaned target causes intermittent 502 errors. Always run `scripts/manage_ec2_alb.sh deregister` before stopping an instance.

### RDS Proxy (code-ready, not yet deployed)

The codebase fully supports RDS Proxy for Lambda → RDS connection pooling:
- `sqs_lambda_worker.py` — detects `RDS_PROXY_HOST` env var and switches to IAM auth token generation
- `template.yaml` — conditional `HasRdsProxy` parameter gates VPC config and `rds-db:connect` IAM policy

RDS Proxy requires a paid AWS account (free-tier restriction). Until deployed, Lambda connects directly via `DATABASE_URL`.

### Railway

Push to a Railway project — `railway.json` configures Dockerfile build, healthcheck, and restart policy.

### Heroku / Generic PaaS

Uses the `Procfile`:
```
web: uvicorn telecom_tower_power_api:app --host 0.0.0.0 --port ${PORT:-8000}
```

---

## CSV Formats

### Tower CSV (`towers_brazil.csv`)

```csv
id,lat,lon,height_m,operator,bands,power_dbm
VIVO_001,-15.7801,-47.9292,45,Vivo,"700MHz,1800MHz",46
```

### Receiver CSV (for batch reports)

```csv
lat,lon,height,gain
-15.8500,-47.8100,12.0,15.0
-15.8700,-47.7900,10.0,12.0
```

Required columns: `lat`, `lon`. Optional: `height` (default 10 m), `gain` (default 12 dBi).

- **≤ 100 rows** → synchronous ZIP response
- **> 100 rows** → background job; poll `GET /jobs/{job_id}` for progress, download via `/jobs/{job_id}/download`

---

## Project Structure

```
TELECOM-TOWER-POWER/
├── telecom_tower_power_api.py   # FastAPI app (all endpoints + auth)
├── telecom_tower_power.py       # Standalone sync engine
├── tower_db.py                  # Database layer (SQLite / PostgreSQL)
├── job_store.py                 # Persistent job queue (batch_jobs table)
├── batch_worker.py              # Background worker process
├── migrate_csv_to_db.py         # CSV → DB migration CLI
├── stripe_billing.py            # Stripe integration + key store
├── pdf_generator.py             # PDF report builder
├── srtm_elevation.py            # SRTM .hgt tile reader (+ Redis L2 cache)
├── srtm_prefetch.py             # Pre-download SRTM tiles per country
├── sqs_lambda_worker.py         # SQS → Lambda batch worker
├── s3_storage.py                # S3 upload/download + presigned URLs
├── load_towers.py               # CSV → API tower loader
├── frontend.py                  # Streamlit UI (API-backed)
├── streamlit_app.py             # Standalone Streamlit (no API needed)
├── towers_brazil.csv            # Sample tower dataset (Brasília)
├── sample_receivers.csv         # Sample receivers for batch testing
├── sample_batch_test.csv        # 20-row batch test CSV
├── grafana_dashboard.json       # Pre-built Grafana dashboard
├── prometheus.yml               # Prometheus scrape config
├── requirements.txt             # Python dependencies
├── LICENSE                      # Commercial license
├── Dockerfile                   # Multi-stage Docker build
├── docker-compose.yml           # Full-stack orchestration (9 services)
├── start.sh                     # Full-stack launcher script
├── railway.json                 # Railway deployment config
├── template.yaml                # SAM/CloudFormation (Lambda + API GW + SQS)
├── Makefile                     # Lambda build (controls package contents)
├── ecs-task-definition.json     # ECS Fargate task (API + worker + EFS)
├── Procfile                     # Heroku/PaaS process file
├── .dockerignore
├── frontend/                    # React + Leaflet SPA
│   ├── src/
│   │   ├── App.jsx              # Main layout (map + signup)
│   │   ├── TowerMap.jsx         # Leaflet map component
│   │   ├── Sidebar.jsx          # Analysis controls + results
│   │   ├── Signup.jsx           # Self-service signup page
│   │   ├── api.js               # API client module
│   │   └── App.css              # Dark theme + mobile responsive
│   ├── public/
│   │   ├── manifest.json        # PWA manifest
│   │   ├── sw.js                # Service worker
│   │   └── icons/               # PWA icons (192 + 512)
│   ├── vite.config.js           # Vite + proxy config
│   └── package.json
├── .streamlit/
│   └── config.toml              # Streamlit dark theme
└── srtm_data/                   # SRTM elevation tiles (.hgt)
```

---

## License

> **This repository is licensed under the TELECOM-TOWER-POWER Proprietary
> Business License — see [LICENSE.md](LICENSE.md). It is NOT open source.**

Copyright (c) 2026 TELECOM-TOWER-POWER. All rights reserved.

- **Code**: Proprietary Business License with automatic conversion to
  Apache-2.0 on **2028-05-01** (see [LICENSE.md](LICENSE.md)).
- **Trained models, geocode caches and derived datasets**: governed by
  [LICENSE-DATA.md](LICENSE-DATA.md) — separate terms, **not** covered by the
  code license.
- **End User License Agreement (EULA SaaS)**: [EULA.md](EULA.md).
- **Data Processing Agreement (LGPD / DPA)**: [DPA-LGPD.md](DPA-LGPD.md).
- **Contributions**: require sign-off per [CONTRIBUTING.md](CONTRIBUTING.md)
  (DCO).
- **Production use, SLAs and enterprise integrations**: require a signed
  License Agreement.

The previous commercial terms (pre-2026-05-01) are preserved for historical
reference in [LICENSE.previous-commercial.md](LICENSE.previous-commercial.md)
and have been **superseded** by [LICENSE.md](LICENSE.md).
