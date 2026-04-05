# TELECOM TOWER POWER

**Production-ready B2B SaaS platform for telecom RF engineering.**
Tower database management, point-to-point link analysis, terrain-aware multi-hop repeater planning, PDF reporting, and self-service billing — all behind a tiered API with Prometheus monitoring.

![Python 3.10](https://img.shields.io/badge/python-3.10-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688)
![License](https://img.shields.io/badge/license-Commercial-red)

---

## Architecture

```
┌──────────────┐   /api proxy    ┌──────────────────────────────────┐
│  React SPA   │ ─────────────▸  │  FastAPI  (telecom_tower_power   │
│  (Leaflet)   │   port 3000     │           _api.py)               │
└──────────────┘                 │                                  │
                                 │  ┌──────────┐ ┌───────────────┐  │
┌──────────────┐  Streamlit UI   │  │ Stripe   │ │  Prometheus   │  │
│ frontend.py  │ ─────────────▸  │  │ billing  │ │  /metrics     │  │
└──────────────┘   port 8501     │  └──────────┘ └───────────────┘  │
                                 │                                  │
                                 │  ┌──────────┐ ┌───────────────┐  │
                                 │  │ SRTM     │ │ Open-Elevation│  │
                                 │  │ .hgt     │ │ API fallback  │  │
                                 │  └──────────┘ └───────────────┘  │
                                 │                                  │
                                 │  ┌──────────────────────────┐    │
                                 │  │ PDF Generator (ReportLab │    │
                                 │  │ + Matplotlib terrain)    │    │
                                 │  └──────────────────────────┘    │
                                 └──────────────────────────────────┘
```

| Component | File(s) | Purpose |
|---|---|---|
| **API** | `telecom_tower_power_api.py` | FastAPI backend — all endpoints, auth, rate limiting |
| **Standalone engine** | `telecom_tower_power.py` | Sync library (no server dependency) |
| **Elevation** | `srtm_elevation.py` | Offline SRTM3 .hgt reader with bilinear interpolation |
| **PDF reports** | `pdf_generator.py` | A4 engineering reports with terrain/Fresnel charts |
| **Billing** | `stripe_billing.py` | Stripe Checkout, webhook handling, key lifecycle |
| **React UI** | `frontend/src/` | Leaflet map, link analysis, repeater planner |
| **Streamlit UI** | `frontend.py` | Alternative dashboard with Folium maps |
| **Tower loader** | `load_towers.py` | Bulk CSV → API ingestion script |

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the API server

```bash
uvicorn telecom_tower_power_api:app --host 127.0.0.1 --port 8000
```

### 3. Load tower data

```bash
python load_towers.py                          # defaults: towers_brazil.csv → localhost:8000
python load_towers.py towers_brazil.csv http://your-host:8000
```

### 4. Launch the React frontend (optional)

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000, proxies /api → backend
```

### 5. Launch the Streamlit frontend (optional)

```bash
streamlit run frontend.py
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

`GET /metrics` exposes Prometheus-compatible metrics:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `http_request_duration_seconds` | Histogram | `method`, `endpoint`, `status` | Request latency (10 buckets: 10 ms – 10 s) |
| `http_requests_total` | Counter | `method`, `endpoint`, `status` | Total request count |
| `rate_limit_hits_total` | Counter | `tier` | Rate-limit 429 rejections |
| `batch_jobs_active` | Gauge | — | Background batch jobs currently running |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SRTM_DATA_DIR` | `./srtm_data` | Path to SRTM `.hgt` tile directory |
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

Starts three services:
- **api** — FastAPI on port 8000 with healthcheck
- **frontend** — Streamlit on port 8501
- **load-towers** — one-shot CSV loader

### Railway

Push to a Railway project — `railway.json` configures Dockerfile build, healthcheck, and restart policy.

### Render

Push to Render — `render.yaml` defines two web services (API + UI) with healthchecks on the free plan.

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
├── stripe_billing.py            # Stripe integration + key store
├── pdf_generator.py             # PDF report builder
├── srtm_elevation.py            # SRTM .hgt tile reader
├── load_towers.py               # CSV → API tower loader
├── frontend.py                  # Streamlit UI (API-backed)
├── streamlit_app.py             # Standalone Streamlit (no API needed)
├── towers_brazil.csv            # Sample tower dataset (Brasília)
├── sample_receivers.csv         # Sample receivers for batch testing
├── sample_batch_test.csv        # 20-row batch test CSV
├── .env.example                 # Environment variable template
├── requirements.txt             # Python dependencies
├── LICENSE                      # Commercial license
├── Dockerfile                   # Multi-stage Docker build
├── docker-compose.yml           # Full-stack orchestration
├── start.sh                     # Full-stack launcher script
├── render.yaml                  # Render deployment config
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
