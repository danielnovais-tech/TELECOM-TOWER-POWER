# 06 — Business Continuity & Disaster Recovery Policy

| | |
|---|---|
| **Owner** | SRE |
| **Review cadence** | Annual; failover drill quarterly; restore drill weekly |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC9.1, A1.2, A1.3 |

## 1. Recovery objectives

| Tier | RTO | RPO |
|---|---|---|
| Free / Starter / Pro | 4 h | 24 h |
| Business | 1 h | 12 h |
| Enterprise | 30 min | 1 h |
| Ultra | 15 min | 15 min |

## 2. Backup strategy

### Production database (Postgres)

- **Cadence:** Nightly `pg_dump` at 05:30 UTC.
- **Destination:** `s3://telecom-tower-power-results/backups/railway-postgres/railway_towers_<TIMESTAMP>.sql.gz`.
- **Retention:** 14 days hot in S3 + S3 lifecycle to Glacier Deep Archive at day 14 with 1-year retention.
- **Encryption:** S3 SSE-KMS.
- **Integrity:** `pg_dump | gzip` runs with `set -euo pipefail` and a size sanity check (>1 KB or fail).
- **Workflow:** `.github/workflows/backup-railway-postgres.yml`.

### Grafana dashboards & data

- **Cadence:** Nightly tar of `grafana_data` volume.
- **Destination:** S3 (same bucket, separate prefix).
- **Workflow:** `.github/workflows/backup-grafana-volume.yml`.

### Code & configuration

- All source in GitHub (`danielnovais-tech/TELECOM-TOWER-POWER`); GitHub provides redundancy.
- IaC + workflows in the same repo; no out-of-band config.

## 3. Verified restore

- **Cadence:** Weekly Mondays 07:15 UTC.
- **Workflow:** `.github/workflows/backup-restore-drill.yml`.
- **Procedure:** Pull the most recent dump from S3 → verify ≤36 h old → restore into ephemeral Postgres 18 container → assert minimum row counts on `towers` (≥100k), `api_keys` (≥1), `alembic_version` (≥1).
- **Failure handling:** Workflow fails loudly; optional SNS notification via `SYNTHETIC_ALERT_TOPIC_ARN`.
- **Evidence:** GitHub Actions workflow run history (CC8.1.2, A1.2.2).

## 4. Failover

- **Primary:** AWS sa-east-1 (ECS Fargate + RDS).
- **Secondary:** Railway (separate provider, same data via async replication).
- **Trigger:** Route 53 health-check failover when 3 consecutive checks fail.
- **Drill:** Quarterly `failover-rotate.yml` exercise; results recorded.
- **Drift detection:** `failover-drift-check.yml` runs daily, alerts on schema or version divergence.

## 5. Single-region risk acceptance

Production runs in a single AWS region (sa-east-1). Multi-region active/active is not currently in scope; this is documented as an accepted residual risk in the risk register, mitigated by the Railway warm-failover and the BCDR plan.

## 6. Tabletop scenarios

Annual exercise must cover at least one of:

- Total loss of sa-east-1.
- Backup corruption discovered during restore drill.
- Compromise of CI/CD credentials.
- Sub-processor outage cascading (e.g., AWS IAM regional incident).

Outputs filed in `evidence/incidents/tabletop/`.
