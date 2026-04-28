# Railway deployment topology

> **Reference only.** Railway does *not* read this file. Per-service config
> lives in each service's own `railway.json` (see paths below). This document
> exists to make the topology, inter-service references, and required
> environment variables auditable from the repo.

Project: `astonishing-harmony` · Environment: `production` · Region: `us-west2`

## Application services (built from this repo)

| Railway service  | Source path             | Process / start command                                                | Public? |
| ---------------- | ----------------------- | ---------------------------------------------------------------------- | ------- |
| `web`            | `Dockerfile` (root)     | `./entrypoint.sh` → uvicorn `telecom_tower_power_api:app` on `$PORT`   | yes     |
| `worker`         | `Dockerfile` (root)     | `rq worker batch_pdfs --url $REDIS_URL` (Procfile `worker`)            | no      |
| `stripe-webhook` | `Dockerfile` (root)     | `./entrypoint.sh` with `SERVICE_TYPE=webhook` → `stripe_webhook_service:app` on `$PORT` | yes |
| `frontend`       | `frontend/Dockerfile`   | `/docker-entrypoint.sh` (nginx serving Vite build)                     | yes     |

Authoritative per-service config:

- [web/railway.json](../web/railway.json)
- [frontend/railway.json](../frontend/railway.json)
- [monitoring/prometheus/railway.json](../monitoring/prometheus/railway.json)
- [monitoring/grafana/railway.json](../monitoring/grafana/railway.json)
- [redis-metrics-collector/railway.json](../redis-metrics-collector/railway.json)

## Infrastructure services (Railway templates)

| Service     | Image / template                                  | Volume                | Port  |
| ----------- | ------------------------------------------------- | --------------------- | ----- |
| `Postgres`  | `ghcr.io/railwayapp-templates/postgres-ssl:18`    | `postgres-volume` 500 MB | 5432 |
| `Redis`     | `redis:8.2.1`                                     | `redis-volume` 500 MB | 6379  |
| `Bucket`    | `railwayapp-templates/minio`                      | `bucket-volume` 5 GB  | 9000  |
| `Loki`     | `MykalMachon/railway-grafana-stack`               | `loki-volume` 5 GB    | 3100  |
| `Tempo`    | `MykalMachon/railway-grafana-stack`               | `tempo-volume` 5 GB   | 3200  |

## Inter-service reference variables

Use Railway's `${{ Service.VAR }}` syntax so the project graph shows the link
*and* the value rotates automatically when an upstream credential changes.

### `web` (FastAPI API)

| Var                     | Value                                                |
| ----------------------- | ---------------------------------------------------- |
| `PORT`                  | `8000`                                               |
| `DATABASE_URL`          | `${{ Postgres.DATABASE_URL }}`                       |
| `REDIS_URL`             | `${{ Redis.REDIS_URL }}`                             |
| `S3_ENDPOINT_URL`       | `http://${{ Bucket.RAILWAY_PRIVATE_DOMAIN }}:9000`   |
| `S3_BUCKET_NAME`        | `${{ Bucket.MINIO_BUCKET }}` (or hard-coded bucket)  |
| `AWS_ACCESS_KEY_ID`     | `${{ Bucket.MINIO_ROOT_USER }}`                      |
| `AWS_SECRET_ACCESS_KEY` | `${{ Bucket.MINIO_ROOT_PASSWORD }}`                  |
| `S3_REGION`             | `us-east-1` (MinIO ignores it; boto3 still requires) |

> Verify the exact var names exposed by the MinIO template under the `Bucket`
> service's *Variables* tab before pasting these — older templates expose
> `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`, newer ones may differ.

### `worker` (RQ background jobs)

Same Postgres / Redis / S3 vars as `web`. Start command (Settings → Deploy):

```
rq worker batch_pdfs --url $REDIS_URL
```

Without an explicit start command on this service, Railway falls back to the
Dockerfile `CMD`, which runs the API — silently producing a *second* uvicorn
instance instead of an RQ worker.

### `stripe-webhook`

| Var                     | Value                                          |
| ----------------------- | ---------------------------------------------- |
| `SERVICE_TYPE`          | `webhook` (required; switches `entrypoint.sh`) |
| `PORT`                  | `8080`                                         |
| `DATABASE_URL`          | `${{ Postgres.DATABASE_URL }}`                 |
| `STRIPE_SECRET_KEY`     | *(secret, user-provided)*                      |
| `STRIPE_WEBHOOK_SECRET` | *(secret, user-provided)*                      |

### `frontend`

| Var            | Value                                              | Notes                                                                                |
| -------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `BACKEND_URL`  | `http://${{ web.RAILWAY_PRIVATE_DOMAIN }}:8000`    | Server-side / nginx proxy. Private domain avoids egress and TLS overhead.            |
| `VITE_API_URL` | `https://${{ web.RAILWAY_PUBLIC_DOMAIN }}`         | Baked into the JS bundle at build time, so it must be the **public** HTTPS URL.      |

`RAILWAY_PUBLIC_DOMAIN` and `RAILWAY_PRIVATE_DOMAIN` return *hostnames only*
(no scheme, no port) — always prefix with `http://` / `https://` and append
`:port` for private URLs.

## Why the project graph shows "unlinked" services

Railway only draws an edge when a downstream service references the upstream
via `${{ Service.VAR }}`. Hard-coded hostnames like `web.railway.internal:8000`
in [monitoring/prometheus/prometheus.yml](../monitoring/prometheus/prometheus.yml)
work at runtime but never produce arrows in the canvas — that's expected for
static Prometheus scrape configs and not a misconfiguration.

## Known duplicates (cleanup backlog)

These were created by repeated "Deploy from GitHub" clicks and now compete:

- **`web` ↔ `TELECOM-TOWER-POWER`** — both run `entrypoint.sh`, so both run
  `alembic upgrade head` on every deploy. Keep `web`; remove the other.
- **`worker` ↔ `rq-worker`** — same image, same job. Keep one, delete the other.
- **`Redis` ↔ `Redis-V-Nm`** — pick the 50 GB volume one if SRTM cache + RQ
  needs the headroom; rewire all consumers to the survivor with `${{ ... }}`.
- **Two Prometheus services** — the custom one in
  [monitoring/prometheus/](../monitoring/prometheus/) is authoritative; the
  Grafana-stack template's bundled Prometheus should be disabled.
- **`grafana-data` (50 GB) volume** — unmounted; safe to delete.
