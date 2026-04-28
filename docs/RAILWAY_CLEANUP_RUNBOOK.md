# Railway production cleanup runbook

> **Scope:** project `astonishing-harmony` / environment `production`. Resolves
> the duplicate-services problem documented in [RAILWAY.md](RAILWAY.md)
> ("Known duplicates" section) — root cause of the 166 recent failures.
>
> **Time budget:** ~45 min, mostly waiting for redeploys.
>
> **Prerequisites:**
> - Railway CLI logged in: `railway login` then `railway link` to
>   `astonishing-harmony/production`.
> - PR [#19](https://github.com/danielnovais-tech/TELECOM-TOWER-POWER/pull/19) merged (correct reference variables on disk).
> - A recent Postgres backup. Verify `scripts/pg-backup-s3.sh` ran in the last
>   24 h (S3 object > 1 KB — see the pipefail/size-check rule).
> - 30 min low-traffic window (drain in progress jobs first).

## Phase 0 — capture current state (read-only, safe anytime)

```bash
./scripts/audit_railway.sh > /tmp/railway-before.txt
```

Commit the output to a gist or paste into the tracking issue so you can diff
against `railway-after.txt` at the end.

## Phase 1 — drain RQ jobs (prevents data loss in Phase 3)

1. **Pause new submissions.** In the Railway dashboard, on `web` set env var
   `RQ_ENQUEUE_DISABLED=1` (the API's `batch_worker.py` should refuse new
   enqueues — verify the flag exists; if not, skip and accept brief queue
   duplication risk).
2. **Wait for the queue to drain.** Watch `redis-cli -u "$REDIS_URL" llen rq:queue:batch_pdfs` until it hits 0.
3. **Stop both worker services** (`worker` and `rq-worker`) via Railway → Service → *Stop*. They'll be re-started in Phase 3.

## Phase 2 — delete the application-service duplicates

> **Order matters:** delete the *unused* twin before reapointing references,
> so consumers can't latch onto the wrong service mid-rotation.

### 2a. `TELECOM-TOWER-POWER` ↔ `web`

`web` is the canonical FastAPI service (it has the public domain
`web-production-90b1f.up.railway.app` and the 24-var Stripe/S3/CORS config).
`TELECOM-TOWER-POWER` is a duplicate created by an earlier "Deploy from
GitHub" click and runs the same `entrypoint.sh`, racing on
`alembic upgrade head`.

1. In the Railway UI, open `TELECOM-TOWER-POWER` → Settings → scroll to
   *Danger Zone* → **Delete Service**. Type the service name to confirm.
2. The service has **no volumes** (verify in Phase 0 output) so nothing else
   needs migrating.
3. Search the codebase for hard-coded references:
   ```bash
   git grep -nE 'TELECOM-TOWER-POWER\.railway\.internal|TELECOM-TOWER-POWER\.up\.railway\.app'
   ```
   Replace any hits with `web.railway.internal` / `web.up.railway.app`. The
   only known one is in [frontend/railway.json](../frontend/railway.json) — the description still mentions
   the old name; update it.

### 2b. `rq-worker` ↔ `worker`

Both run the same image. `worker` is canonical (referenced from the new
[RAILWAY.md](RAILWAY.md)).

1. Capture `rq-worker`'s start command and env vars from the Railway UI →
   *Variables* tab — sanity check they match `worker`. Anything missing on
   `worker`, copy over now.
2. Set `worker`'s start command (Settings → Deploy → *Custom Start Command*):
   ```
   rq worker batch_pdfs --url $REDIS_URL
   ```
   Without this, `worker` falls back to the Dockerfile `CMD` and runs the API.
3. Delete `rq-worker` (Settings → Danger Zone → Delete).

## Phase 3 — Redis consolidation

You have **two Redis services** with overlapping consumers:

| Service       | Volume                | Size  | Likely role                      |
| ------------- | --------------------- | ----- | -------------------------------- |
| `Redis`       | `redis-volume`        | 500 MB | Original; default `REDIS_URL`    |
| `Redis-V-Nm`  | `redis-volume-CYB7`   | 50 GB | Created later for SRTM cache    |

### Decide which to keep

Run on each:
```bash
railway run -s Redis      'redis-cli -u $REDIS_URL info keyspace'
railway run -s Redis-V-Nm 'redis-cli -u $REDIS_URL info keyspace'
```

- If `Redis-V-Nm` has the SRTM cache + RQ keys (`rq:queue:*`,
  `srtm:tile:*`), **keep `Redis-V-Nm`** (50 GB headroom matters for SRTM).
- If `Redis` has them, **keep `Redis`**.

### Migrate to the survivor (only if the active data is in the loser)

```bash
# Dump from loser
railway run -s <LOSER> 'redis-cli -u $REDIS_URL --rdb /tmp/dump.rdb'
railway run -s <LOSER> 'cat /tmp/dump.rdb' > /tmp/redis-dump.rdb

# Stop ALL consumers (web, worker, stripe-webhook) BEFORE restoring,
# else writes split-brain.
for svc in web worker stripe-webhook; do
  railway service stop -s "$svc"
done

# Restore to survivor (requires shell access; if not available, use
# `redis-cli --pipe` over the public TCP proxy with a one-shot script)
railway run -s <SURVIVOR> 'redis-cli -u $REDIS_URL FLUSHALL'
cat /tmp/redis-dump.rdb | railway run -s <SURVIVOR> 'redis-cli -u $REDIS_URL --pipe'
```

### Rewire references

For each consumer (`web`, `worker`, `stripe-webhook`), in Railway UI →
Variables, change `REDIS_URL` to:

```
${{ <SURVIVOR>.REDIS_URL }}
```

Save → Railway will redeploy each service automatically. Wait for all three
to go green.

### Delete the loser

1. `redis-metrics-collector-a115` (or whichever points at the loser) →
   Delete.
2. The loser Redis service → Delete.
3. The loser's volume (`redis-volume` or `redis-volume-CYB7`) → Project
   Settings → Volumes → Delete. **Confirm it's the right volume name** —
   Railway does not undo this.

## Phase 4 — Prometheus consolidation

Two Prometheus services scrape the same `*.railway.internal` targets:

- **Custom** (build from [monitoring/prometheus/](../monitoring/prometheus/), volume `prometheus-data` 50 GB) — keep.
- **Grafana-stack template** (volume `prometheus-volume` 5 GB) — delete.

1. In Grafana (`Console` service → public domain), edit the *Prometheus*
   datasource to point at `http://prometheus.railway.internal:9090` (the
   custom one). It already does per
   [monitoring/grafana/provisioning/datasources/prometheus.yml](../monitoring/grafana/provisioning/datasources/prometheus.yml) — verify
   no override exists in the UI.
2. Delete the template's `Prometheus` service.
3. Delete the orphan `prometheus-volume` (5 GB) afterwards.

## Phase 5 — orphan volume sweep

```bash
# List unmounted volumes
railway volume list --json | jq '.[] | select(.attachedToService == null)'
```

Expected hit: `grafana-data` (50 GB). Any others, investigate before deleting
— a volume can briefly look unattached during a service rebuild.

```bash
railway volume delete grafana-data
```

## Phase 6 — `Console` healthcheck fix

The `Console` service has 25 recent failures. Its `railway.json` declares
`healthcheckPath: /health`, but if it's actually MinIO Console (not Grafana),
that path doesn't exist on MinIO Console (which serves `/api/v1/login` /
static assets).

1. `curl -sI https://console-production-8de46.up.railway.app/health` —
   if 404, the healthcheck is wrong.
2. In Railway → `Console` → Settings → Healthcheck:
   - For Grafana: `/api/health`
   - For MinIO Console: leave it empty (Railway falls back to TCP probe)

## Phase 7 — verify

```bash
./scripts/audit_railway.sh > /tmp/railway-after.txt
diff -u /tmp/railway-before.txt /tmp/railway-after.txt
```

Then on Railway → project → *Activity* tab, confirm:

- 4 application services online: `web`, `worker`, `stripe-webhook`, `frontend`.
- 3 infrastructure services: `Postgres`, `Redis` (one), `Bucket`.
- 4 observability services: `Prometheus` (custom), `Loki`, `Tempo`, `Console`/`Grafana`, `redis-metrics-collector` (one), `postgres-metrics-collector`.
- No volumes unattached.
- "Recent failures" counter on each service trending toward 0 over the next
  hour.

## Rollback

Every step in this runbook is destructive *only at the explicit Delete*.
Until then, all services are merely stopped and can be re-started from the
Railway UI. If something goes wrong:

1. **Don't panic-delete anything else.** Re-start the original duplicates
   from the *Stopped* tab.
2. Postgres restore from `pg-backup-s3.sh`'s latest S3 object: see
   `scripts/pg-backup-s3.sh` and the size-check note in user memory
   (`backup-pipefail-trap.md`).
3. Redis is cache + queue — losing it costs in-flight jobs but no durable
   state. Reset all consumers and let RQ rebuild from Postgres truth.
