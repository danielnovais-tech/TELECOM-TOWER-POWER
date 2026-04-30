# Arquitetura

Visão de alto nível da plataforma TELECOM TOWER POWER em produção.

## Topologia geral

```mermaid
flowchart TB
    subgraph Clients
        Web[Web SPA · Streamlit · curl/SDK]
    end

    subgraph Edge["Edge / Ingress"]
        R53[Route 53]
        ALB[AWS ALB · sa-east-1]
        Caddy[Caddy reverse-proxy<br/>EC2 EIP 18.229.14.122]
        RailwayRouter[Railway router]
    end

    subgraph Compute
        ECS[ECS Fargate<br/>prod-of-record]
        EC2[EC2 Docker Compose<br/>frontend · grafana · prometheus]
        Railway[Railway service web<br/>warm failover]
        Lambda[AWS Lambda<br/>SQS batch consumer]
    end

    subgraph App["FastAPI app · telecom_tower_power_api.py"]
        Auth[Auth · Rate-limit · Audit · Metrics]
        Towers[/towers · /towers/nearest/]
        Analyze[/analyze · Fresnel · LOS · RSSI/]
        Plan[/plan_repeater Dijkstra/]
        Predict[/coverage/predict ridge-v1/]
        Batch[/batch_reports · /jobs/]
        Bedrock[/bedrock chat · compare/]
    end

    subgraph Data
        RDS[(PostgreSQL 18.3<br/>Railway prod / SQLite dev)]
        Redis[(ElastiCache Redis<br/>hop cache · jobs · rate-limits)]
        S3[(S3 telecom-tower-power-results<br/>models · reports · backups)]
        SRTM[SRTM tiles<br/>local + Redis L2]
    end

    subgraph External
        Bedrock2[AWS Bedrock]
        Stripe[Stripe billing]
        Cognito[AWS Cognito OIDC]
        OpenCellID[OpenCelliD / ANATEL]
    end

    subgraph Observability
        Prom[Prometheus<br/>13 alert rules]
        Graf[Grafana]
        AM[Alertmanager → Slack · PagerDuty]
        Loki[Loki]
    end

    Web --> R53
    R53 --> ALB
    R53 --> Caddy
    R53 --> RailwayRouter
    ALB --> ECS
    Caddy --> EC2
    RailwayRouter --> Railway
    ECS --> Auth
    EC2 --> Auth
    Railway --> Auth
    Auth --> Towers & Analyze & Plan & Predict & Batch & Bedrock
    Towers & Analyze & Plan & Predict & Batch --> RDS
    Plan & Predict --> Redis
    Predict --> SRTM
    Predict -. boot eager refresh_from_s3 .-> S3
    Batch --> S3
    Batch -. enterprise tier .-> Lambda
    Lambda --> S3
    Lambda --> RDS
    Bedrock --> Bedrock2
    Auth --> Stripe
    Auth --> Cognito
    ECS & EC2 --> Prom
    Prom --> Graf
    Prom --> AM
    EC2 --> Loki

    classDef ext fill:#fef3c7,stroke:#d97706
    class Bedrock2,Stripe,Cognito,OpenCellID ext
```

## Pipeline de ML — *terrain-aware* signal predictor

```mermaid
flowchart LR
    subgraph CI["Nightly CI · retrain_coverage_model.py"]
        Synth[Synthetic generator<br/>n=20000, seed=13]
        Real[(link_observations<br/>cell_signal_samples)]
        Train[train_model<br/>l2=0.3, ridge-v1]
    end

    Synth --> Train
    Real --> Train
    Train -->|np.savez| NPZ[coverage_model.npz<br/>17 features · ~1.8 KB]
    NPZ -->|aws s3 cp| S3M[(s3://telecom-tower-power-results/<br/>models/coverage_model.npz)]
    NPZ -->|git commit| Repo[(repo baseline)]

    subgraph Boot["Container boot · entrypoint.sh"]
        Refresh[refresh_from_s3]
        Load[CoverageModel.load<br/>np.load allow_pickle=False]
        Log["log: Coverage model active:<br/>version=ridge-v1 rmse_db=12.94 n_train=20000"]
    end

    S3M --> Refresh
    Refresh --> Load
    Load --> Log

    subgraph Serve["Request /coverage/predict"]
        Feat[Build 17 features<br/>SRTM profile · log d · fresnel ratio · terrain σ]
        Inf[Ridge inference<br/>w·x + bias]
        Conf["confidence = clip(1 - (rmse_db - 8)/20, 0.3, 0.9)"]
    end

    Load -.cached.-> Inf
    Feat --> Inf --> Conf
```

## Ciclo de vida de uma requisição `/coverage/predict`

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R53 as Route 53
    participant ALB
    participant API as FastAPI<br/>(ECS task)
    participant K as key_store_db<br/>(Redis cache)
    participant SRTM as srtm_elevation
    participant ML as CoverageModel<br/>(in-process)
    participant Aud as audit_log

    C->>R53: POST api.telecomtowerpower.com.br/coverage/predict
    R53->>ALB: route by host
    ALB->>API: HTTP/2
    API->>K: verify_api_key (X-API-Key / Bearer)
    K-->>API: tier=pro, owner=...
    API->>API: rate-limit check (6 req/min pro)
    API->>SRTM: terrain profile along link
    SRTM-->>API: elevation samples
    API->>ML: predict(features[17])
    ML-->>API: signal_dbm, rmse_db
    API->>API: confidence = clip(1 - (rmse_db-8)/20, .3, .9)
    API->>Aud: row(action=predict, tenant, ts)
    API-->>C: {model_source:"local-model", model_version:"ridge-v1",<br/>signal_dbm:-31.4, confidence:0.75}
```

## Camadas

### Verified summary (`Apr 2026`)

| Layer | Implementation |
|---|---|
| **Primary API** | FastAPI (Python 3.13) — prod traffic via Caddy on EC2 t3.small (sa-east-1) reverse-proxied to Railway; ECS Fargate task-def rev 44 kept warm. Local stack: **18-service** Docker Compose. |
| **Database** | PostgreSQL **18.3** on Railway (managed) — **140,498** towers (verified from nightly dump). |
| **Cache & Queue** | Redis 8.6.2 (SRTM cache, hop cache, jobs, rate-limits). |
| **Batch** | Hybrid: ≤100 rows sync; >100 rows async via SQS → Lambda → S3. |
| **AI & ML** | AWS Bedrock (Claude / Titan / Llama) for chat; ridge-v1 (`coverage_predict.py`, 17 features). |
| **Frontend** | React PWA served by Nginx 1.30 + Streamlit + MkDocs (Material). |
| **Monitoring** | Prometheus v3.11.2 + Grafana 13.0.1 + Alertmanager v0.32.0 + Jaeger 1.76.0 (OTLP). |
| **Failover** | Railway active for `api.*`; ECS Fargate kept warm; Route 53 latency-based failover **planned**. |
| **Backups** | Nightly: Grafana volume → S3 (~23.05 MB), Railway Postgres → S3 (~1.78 MB gzip, weekly verified restore). |
| **CI/CD** | **19** GitHub Actions workflows (deploy, backup, drift, failover, retrain, secrets sync, …). |
| **TLS** | ACM on ALB (sa-east-1) terminates HTTPS; Caddy on EC2 serves :80 origin only. |

| Layer | Components | Function |
|---|---|---|
| **Edge** | ALB · Caddy · Railway router · Route 53 (DNS failover) | TLS termination, host routing, health checks |
| **Compute** | ECS Fargate (primary) · EC2 + Docker Compose · Railway · AWS Lambda (`sqs_lambda_worker.py`) | API + workers + bursty batch consumer |
| **Application** | FastAPI (`telecom_tower_power_api.py`) + Streamlit (`frontend.py`) + React SPA | HTTP / WebSocket / SSE surfaces |
| **Data** | Railway PostgreSQL 18.3 · ElastiCache Redis · S3 (artifacts + backups) · SRTM cache (`hop_cache.py`, `srtm_elevation.py`) | Persistent state, hot caches, terrain |
| **ML** | ridge-v1 in `.npz` · S3 hot-pull · nightly retrain in CI · Bedrock for scenarios | Terrain-aware signal prediction + GenAI |
| **Async** | SQS priority queue · Lambda consumer · `batch_worker.py` · `repeater_jobs_store.py` (Redis) | Long PDF batches and ≥4-hop planning |
| **Auth** | API keys (`key_store_db.py`) · Cognito OIDC + Bearer · per-tier rate limits · audit log | OWASP-Top-10 hardening |
| **Observability** | Prometheus (13 rules) · Grafana · Alertmanager · OpenTelemetry · Loki | Metrics, dashboards, paging (Slack + PagerDuty) |
| **CI/CD** | 16 GitHub Actions workflows · BuildKit cache · secret sync via SSM · weekly restore drill | Push-to-deploy, nightly retrain, restore drill |
| **Backups** | Postgres + Grafana volume → S3 nightly (14d retention) · weekly verified restore | DR, RPO ≈ 24h |

## 🧠 Key Algorithms

| Feature | Implementation |
|---|---|
| **Link budget** | Free-space path loss + Fresnel zone + earth curvature (effective radius `k=4/3`). See [pdf_generator.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/pdf_generator.py) (`_free_space_path_loss`, first-zone envelope, `earth_bulge`). |
| **Repeater planning** | **Bottleneck-shortest-path** Dijkstra (min-max) over candidate towers; relaxation `new_bottleneck = max(bottleneck, effective_loss)` with terrain-scored `effective_loss` ([telecom_tower_power_api.py#L731](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/telecom_tower_power_api.py#L731)). |
| **PDF reports** | ReportLab for tables/layout + Matplotlib for the terrain + Fresnel-zone plot ([pdf_generator.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/pdf_generator.py)). |
| **ML signal prediction** | Ridge regression on **17 engineered features** (SRTM profiles, slope, obstruction count, min Fresnel ratio, log/interaction terms). Trained on synthetic physics (`_physics_signal`) + log-normal shadow fading, with optional real-data up-weighting. **Fallback chain:** SageMaker endpoint → local `.npz` model → deterministic physics ([coverage_predict.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/coverage_predict.py) — `_FEATURE_NAMES`, `predict_signal`). |

## 🗄️ Data Pipeline

**Tower sources**

- **ANATEL** (official) — 105,240 unique stations (Postgres prod count).
  Geocoded via IBGE municipality centroids + small random jitter (~800 m)
  so same-city towers don't stack
  ([load_anatel.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/load_anatel.py)).
- **OpenCelliD** (crowdsourced) — 35,248 GPS-tagged cells
  ([load_opencellid.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/load_opencellid.py)).

**Geocoding**

- Pre-built lookup table of ~5,570 IBGE municipalities in
  `municipios_brasileiros.csv` → centroid + ±jitter.
- Cache misses fall back to Nominatim (rate-limited to 1.1 req/s).
- **ANATEL→OpenCelliD snap pass** (
  [snap_anatel.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/snap_anatel.py)):
  for every `ANATEL_*` tower, find the closest `OCID_*` tower of the
  **same operator** within a configurable radius (default **5 km**) using
  a 0.05° spatial bucket index + haversine distance; rewrite `lat`/`lon`
  to the candidate's, keeping the `id`. 3×3 bucket lookup, O(N) overall.
  CLI: `python snap_anatel.py [--max-km 5.0] [--dry-run]`.

**SRTM elevation tiles (90 m)**

- Local `.hgt` files in `./srtm_data/` (L1 in-process cache).
- Optional Redis L2 cache: raw `.hgt` blobs, 7-day TTL
  ([srtm_elevation.py](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/srtm_elevation.py)
  — key `srtm:<tile>`).
- No Open-Elevation API fallback today; missing tile → `ValueError`.

**Nightly sync (AWS RDS → Railway)**

- [.github/workflows/sync-towers.yml](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/blob/main/.github/workflows/sync-towers.yml),
  cron `05:00 UTC`.
- SSM port-forward via EC2 bastion (no SG ingress) → `localhost:15432`
  → `RDS:5432`; runs
  `import_towers.py --source-env AWS --target-env RAILWAY --delete-missing`.

## S3 — single source of truth

```
s3://telecom-tower-power-results/
├── models/coverage_model.npz          ← ML artifact (ridge-v1, 1850 B)
├── reports/{tenant}/{job_id}.zip      ← async batch outputs
├── backups/postgres/YYYY-MM-DD.sql.gz ← nightly pg_dump
└── backups/grafana/YYYY-MM-DD.tar.gz  ← nightly Grafana volume snapshot
```
