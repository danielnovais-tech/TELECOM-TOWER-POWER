# TELECOM TOWER POWER

**Production-ready B2B SaaS platform for telecom RF engineering.**
Tower database management, point-to-point link analysis, terrain-aware multi-hop repeater planning, PDF reporting, and self-service billing — all behind a tiered API with Prometheus monitoring.

> **140,906 towers** across Brazil — 105,240 from ANATEL (12 operators, 5,570 municipalities) + OpenCelliD crowd-sourced data. Default tower parameters: 35 m height, 43 dBm power, 700/1800 MHz bands.

![Python 3.10](https://img.shields.io/badge/python-3.10-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688)
![License](https://img.shields.io/badge/license-Commercial-red)

---

## Architecture

```
                           ┌──────────────────────────────────────────────────────┐
                           │                 Docker Compose Stack                 │
                           │                                                     │
┌──────────────┐           │  ┌──────────────────────────────────┐               │
│  React SPA   │───────────│─▸│  FastAPI  (telecom_tower_power   │               │
│  (Leaflet)   │ port 3000 │  │           _api.py)  :8000        │               │
└──────────────┘           │  │                                  │               │
                           │  │  Auth ─▸ Rate Limiter ─▸ Metrics │               │
┌──────────────┐           │  │  CORS ─▸ Security Headers        │               │
│ Streamlit UI │───────────│─▸│  Stripe billing │ Prometheus     │               │
│ frontend.py  │ port 8501 │  └────────┬────────┴────────────────┘               │
└──────────────┘           │           │                                         │
                           │           ▼                                         │
                           │  ┌─────────────────┐   ┌────────────────────┐       │
                           │  │  PostgreSQL 16   │   │  Batch Worker      │       │
                           │  │  (tower_db.py)   │◂──│  (batch_worker.py) │       │
                           │  │  towers, jobs    │   │  polls job queue   │       │
                           │  └─────────────────┘   └────────────────────┘       │
                           │                                                     │
                           │  ┌─────────────────┐   ┌────────────────────┐       │
                           │  │  Prometheus      │──▸│  Grafana           │       │
                           │  │  :9090           │   │  :3001             │       │
                           │  └─────────────────┘   └────────────────────┘       │
                           └──────────────────────────────────────────────────────┘

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

**Demo keys** (built-in):

| Key | Tier |
|---|---|
| `demo-key-free-001` | Free |
| `demo-key-pro-001` | Pro |
| `demo-key-enterprise-001` | Enterprise |

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

### Example: Analyze a link

```bash
# Register a tower
curl -X POST http://localhost:8000/towers \
  -H "X-API-Key: demo-key-pro-001" \
  -H "Content-Type: application/json" \
  -d '{"id":"T1","lat":-15.78,"lon":-47.93,"height_m":45,"operator":"Vivo","bands":["700MHz"],"power_dbm":46}'

# Analyze link to a receiver
curl -X POST "http://localhost:8000/analyze?tower_id=T1" \
  -H "X-API-Key: demo-key-pro-001" \
  -H "Content-Type: application/json" \
  -d '{"lat":-15.85,"lon":-47.81,"height_m":10,"antenna_gain_dbi":12}'
```

### Example: Batch PDF reports

```bash
curl -X POST "http://localhost:8000/batch_reports?tower_id=VIVO_001" \
  -H "X-API-Key: demo-key-pro-001" \
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
   - **SECONDARY** → Railway (`web-production-90b1f.up.railway.app`, TTL 60 s)

If the ALB health check fails, Route 53 automatically routes traffic to Railway within ~60 s.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(none → SQLite)* | PostgreSQL connection string; omit for local SQLite |
| `SRTM_DATA_DIR` | `./srtm_data` | Path to SRTM `.hgt` tile directory |
| `SRTM_REDIS_URL` | *(none)* | Redis URL for L2 SRTM tile cache (e.g. `redis://redis:6379/0`) |
| `CORS_ORIGINS` | `https://app.telecomtowerpower.com` | Comma-separated allowed CORS origins |
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

### Render

Push to Render — `render.yaml` defines:
- **PostgreSQL database** — provisioned automatically
- **API web service** — seeds towers on build, connects to PG via `DATABASE_URL`
- **Background worker** — polls the job queue
- **Streamlit UI** — frontend web service

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
├── render.yaml                  # Render deployment config (PG + worker)
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

Copyright (c) 2025 DANIEL AZEVEDO NOVAIS. All rights reserved.

This software is proprietary. Commercial use requires a signed license agreement.
See [LICENSE](LICENSE) for details.
