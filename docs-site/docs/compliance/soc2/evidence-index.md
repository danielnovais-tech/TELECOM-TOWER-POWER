# Evidence Index — SOC 2 Type II

> Where to find evidence for each control during the audit period.
> All paths assume the audit's read-only IAM role `soc2-auditor` (issued just-in-time).

## Live (continuous) evidence

| Control IDs | Source | Access path | Sampling |
|---|---|---|---|
| CC4.1.1, CC7.2.1 | Prometheus alert history | `https://prometheus.telecomtowerpower.com.br` (read-only Grafana) | Last 90 days, all firings |
| CC4.1.2 | Synthetic monitor runs | GitHub Actions: `synthetic-monitor.yml` workflow runs | Last 90 days, all runs |
| CC6.1.1 | Cognito MFA enforcement | AWS Console → Cognito → User Pool `sa-east-1_15uR6sR9o` | Current config + CloudTrail config-change history |
| CC6.1.2 | API authentication code | `telecom_tower_power_api.py` (`verify_api_key` decorator) | Current `main` HEAD + git log |
| CC6.6.1 | TLS configuration | SSL Labs scan output (saved monthly) + ACM cert config | 12 monthly scans |
| CC6.7.1 | Encryption at rest | AWS Config rule `encrypted-volumes`, `s3-bucket-server-side-encryption-enabled`, `rds-storage-encrypted` | Continuous compliance feed |
| CC7.1.1 | CVE patching SLA | GitHub Dependabot alerts + ECR image-scan findings | All findings during period |
| CC7.3.1 | Incident pages | PagerDuty incident export | All P1/P2 during period |
| CC8.1.1 | PR reviews | GitHub branch protection settings + merged PR audit log | Sample 25 |
| CC8.1.2 | Workflow runs | GitHub Actions: all 16 workflows | Last 90 days, all runs |
| A1.2.1 | Nightly backups | S3: `s3://telecom-tower-power-results/backups/railway-postgres/` | Last 90 dumps |
| A1.2.2 | Verified restore drill | GitHub Actions: `backup-restore-drill.yml` | Last 12 weekly runs |
| C1.1.2 | Tenant isolation | `models.py` + `test_suite.py::test_idor_*` | Test suite output (CI) |
| PI1.3.1 | Audit log | DB table `audit_log` (queryable via `/tenant/audit`) | Sample 25 entries |

## Periodic evidence

| Control IDs | Cadence | Storage |
|---|---|---|
| CC1.1.1, CC1.4.1, CC1.5.1, CC6.3.1 | Annual / per-event | `evidence/hr/` (encrypted S3 prefix; HR-only access) |
| CC1.2.1 | Quarterly | `evidence/governance/board-minutes/` |
| CC2.3.2, CC4.2.1, CC7.4.1, CC7.5.1 | Per-event | `evidence/incidents/` |
| CC3.1.1 | Annual | `evidence/risk/risk-register-YYYY.pdf` |
| CC6.4.1 | Quarterly | `evidence/access-reviews/YYYY-Q*.pdf` |
| CC9.2.1 | Annual | `evidence/vendors/YYYY-vendor-reviews/` |
| A1.1.1 | Quarterly | `evidence/capacity/YYYY-Q*.pdf` |
| A1.3.2 | Quarterly | GitHub Actions: `failover-rotate.yml` |
| P5.1.1 | Per-request | Ticket system export (`legal@` mailbox) |

## Per-incident evidence

For every P1/P2 incident during the audit period, the following must exist:

1. **Detection record** — PagerDuty incident or Slack `#sec-incidents` post.
2. **Timeline** — minute-by-minute log in the postmortem doc.
3. **Customer notification** (if applicable) — copy of email/banner posted ≤ 72 h after confirmation.
4. **Postmortem** — published within 5 business days; includes root cause, impact, corrective actions with owners + due dates.
5. **Corrective action closure** — linked PRs / tickets showing each action item resolved.

Storage: `evidence/incidents/<YYYY-MM-DD-slug>/`

## Sampling instructions for auditor

- **Population-based tests** (CC1.1.1, CC1.4.1, CC6.2.1, CC6.3.1, CC8.1.1, CC8.1.3): sample N=25 per AICPA AT-C 205.A22 unless population <25, in which case full population.
- **Continuous controls** (CC4.1.x, CC6.6.1, CC6.7.1, A1.2.x, PI1.x): inspect ≥1 sample/day across the period (e.g., 90 samples for a 90-day window).
- **Per-event controls** (CC2.3.x, CC7.4.1, CC7.5.1, A1.3.2): full population.

## Pre-saved CloudWatch Logs Insights queries

Located in `evidence/queries/`:

```
audit-log-by-tenant.cwlquery
failed-auth-attempts.cwlquery
admin-actions.cwlquery
secret-rotation-events.cwlquery
backup-restore-drill-results.cwlquery
```

Each query is parameterized for `start`, `end`, and (where applicable) `tenant_id`.
