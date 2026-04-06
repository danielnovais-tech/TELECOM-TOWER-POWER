# TELECOM TOWER POWER

**Production-ready B2B SaaS platform for telecom RF engineering.**
Tower database management, point-to-point link analysis, terrain-aware multi-hop repeater planning, PDF reporting, and self-service billing — all behind a tiered API with Prometheus monitoring.

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
| **Batch worker** | `batch_worker.py` | Background process — polls jobs, generates PDF ZIPs |
| **DB migration** | `migrate_csv_to_db.py` | CLI to load tower CSV into the database |
| **Schema versioning** | `alembic.ini`, `migrations/` | Alembic database migrations |
| **Standalone engine** | `telecom_tower_power.py` | Sync library (no server dependency) |
| **Elevation** | `srtm_elevation.py` | Offline SRTM3 .hgt reader with bilinear interpolation |
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
| Requests/min | 10 | 100 | 1,000 |
| Max towers | 20 | 500 | 10,000 |
| PDF export | — | ✓ | ✓ |
| Batch reports | — | ✓ | ✓ |

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
| `GET` | `/jobs/{job_id}` | — | Poll background batch job status |
| `GET` | `/jobs/{job_id}/download` | — | Download completed batch job ZIP |
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
- **Fresnel zone clearance** — 1st Fresnel radius computed per-link
- **Terrain-aware LOS** — 30-point elevation profile (SRTM3 + Open-Elevation API fallback)
- **Link budget** — TX power, antenna gains, cable losses, fade margin

### Multi-Hop Repeater Planning

The `plan_repeater` endpoint uses a **terrain-aware Dijkstra** algorithm:

1. Generate candidate repeater sites from existing towers
2. Pre-compute hop costs: FSPL + obstruction penalty (up to ~20 dB for Fresnel clearance < 0.6)
3. Find the optimal bottleneck path minimizing worst-case hop cost
4. Respect configurable `max_hops` constraint (default 3)

### Elevation Data

Three-layer resolution with automatic failover:

| Priority | Source | Resolution | Latency |
|---|---|---|---|
| 1 | In-memory cache | exact | ~0 ms |
| 2 | SRTM `.hgt` tiles | ~90 m | ~1 ms |
| 3 | Open-Elevation API | variable | ~200 ms |

Place `.hgt` files in `./srtm_data/` (or set `SRTM_DATA_DIR`).

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

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(none → SQLite)* | PostgreSQL connection string; omit for local SQLite |
| `SRTM_DATA_DIR` | `./srtm_data` | Path to SRTM `.hgt` tile directory |
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

Starts seven services:
- **postgres** — PostgreSQL 16 database
- **api** — FastAPI on port 8000 with healthcheck
- **worker** — Background batch job processor
- **frontend** — Streamlit on port 8501
- **load-towers** — one-shot CSV → DB seeder
- **prometheus** — Metrics scraper on port 9090
- **grafana** — Dashboards on port 3001

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
├── srtm_elevation.py            # SRTM .hgt tile reader
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
├── docker-compose.yml           # Full-stack orchestration (7 services)
├── start.sh                     # Full-stack launcher script
├── render.yaml                  # Render deployment config (PG + worker)
├── railway.json                 # Railway deployment config
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
