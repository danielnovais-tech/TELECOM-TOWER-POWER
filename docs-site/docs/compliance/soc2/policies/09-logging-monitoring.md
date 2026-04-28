# 09 — Logging & Monitoring Policy

| | |
|---|---|
| **Owner** | SRE + Security |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC2.2, CC4.1, CC4.2, CC7.2, CC7.3, PI1.3 |

## 1. What we log

| Source | Content | Retention |
|---|---|---|
| Application access log | Method, path, status, latency, tier, tenant_id | 90 days hot in CloudWatch; 1 year archive |
| Audit log (DB `audit_log`) | Tenant action, actor, params, IP, user-agent, timestamp | 5 years (LGPD-aligned) |
| AWS CloudTrail | All AWS API calls, identity, source IP | 1 year hot, 7 years archive in S3 |
| AWS Config | Resource configurations + compliance state | 1 year |
| Stripe webhook log | Event ID, signature validity, processing outcome | 5 years |
| Workforce auth events (Cognito) | Sign-in, MFA, failures | 1 year |
| Kernel/container logs | Crashes, OOM events | 30 days |
| Backup workflow runs | Dump size, duration, S3 location, drill outcome | 1 year (GitHub Actions retention) |

## 2. What we don't log

- Plaintext passwords (we don't accept passwords; we use Cognito and Bearer tokens).
- Full Stripe webhook payload (only event ID + result; signature-validated).
- Customer-supplied free-text fields beyond a hash, where possible.

## 3. Centralization

- Application logs ship to CloudWatch Logs.
- Audit log lives in Postgres, queryable via `/tenant/audit` (per-tenant) and via direct DB read (admin only).
- Critical/security-relevant events also forwarded to a separate SIEM-style log group (`/aws/ttp/security`) with restricted IAM.

## 4. Monitoring & alerting

- **Prometheus:** scrapes all services; 12 alert rules cover 5xx rate, p95 latency, queue depth, ECS task health, cert expiry, disk pressure, restore-drill failure, Lambda errors.
- **Grafana:** dashboards for API, infra, billing, sales (see Sales Overview dashboard).
- **Alertmanager:** routes by severity:
  - `severity=critical` → PagerDuty (Events API v2, `send_resolved=true`) → 24/7 on-call.
  - `severity=warning` → Slack `#sec-warnings`.
  - `severity=info` → Slack `#sec-info`.
- **External URL:** `https://alerts.telecomtowerpower.com.br` so alert links work from PagerDuty/Slack.

## 5. Synthetic monitoring

- GitHub Actions cron (`synthetic-monitor.yml`) runs every few minutes:
  - HTTPS GET on `api.*`, `app.*`, `docs.*` health endpoints.
  - On failure, page on-call.

## 6. Log integrity

- CloudWatch Log groups enabled for AWS-managed integrity (KMS-encrypted, immutable).
- Audit log table is append-only (DB privileges restrict UPDATE/DELETE on rows older than 1 day).
- S3 archival uses Object Lock (compliance mode) for the audit-log archive prefix.

## 7. Time synchronization

- All AWS resources use AWS NTP (`time.aws.com`); EC2 instances run `chronyd`.
- Logs include timestamps in UTC, ISO 8601 with sub-second precision.

## 8. Review

- **Daily:** automated alert review (anything unacked > 24 h triggers escalation).
- **Weekly:** SRE staff meeting reviews any P3+ events.
- **Monthly:** Security lead reviews authentication failure spikes, IAM anomalies (Access Analyzer, GuardDuty).
- **Quarterly:** access review per [`02-access-control.md`](02-access-control.md).
