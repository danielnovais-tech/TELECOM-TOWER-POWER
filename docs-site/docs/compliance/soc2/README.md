# SOC 2 Type II Readiness Package — TELECOM TOWER POWER

> **Status:** Ready for auditor walkthrough.
> **Scope:** TELECOM TOWER POWER SaaS platform (api.telecomtowerpower.com.br + supporting AWS infrastructure in `sa-east-1`).
> **Trust Services Criteria in scope:** Security (CC), Availability (A), Confidentiality (C), Processing Integrity (PI), Privacy (P).
> **Reporting period (target):** Q3 2026 — Q4 2026 (3-month minimum window, 9-month preferred for first Type II).

## How to use this package

1. Auditor (or pre-audit reviewer) reads [`control-matrix.md`](control-matrix.md) — the canonical inventory of every control, mapped to its TSC criterion, AICPA point-of-focus, owner, evidence location, and test procedure.
2. Each policy in [`policies/`](policies/) has been reviewed and approved by the CTO. Review/approval cadence is annual; deltas are tracked in git.
3. Evidence (logs, screenshots, configs, ticket exports) is indexed in [`evidence-index.md`](evidence-index.md). Live evidence is generated automatically by the platform itself — see the "Continuous monitoring" column.
4. Gap analysis (residual work) is in [`gap-analysis.md`](gap-analysis.md). Closed gaps move to the matrix.

## Document set

```
docs-site/docs/compliance/soc2/
├── README.md                          ← (this file)
├── control-matrix.md                  ← canonical control inventory (76 controls)
├── evidence-index.md                  ← where to find evidence for each control
├── gap-analysis.md                    ← residual gaps + remediation owners
├── system-description.md              ← AICPA-required §3 narrative
├── trust-criteria-mapping.md          ← TSC → AICPA point-of-focus → control IDs
├── continuous-monitoring.md           ← which controls are evidenced automatically
└── policies/
    ├── 01-information-security.md
    ├── 02-access-control.md
    ├── 03-change-management.md
    ├── 04-incident-response.md
    ├── 05-vendor-management.md
    ├── 06-business-continuity.md
    ├── 07-data-classification.md
    ├── 08-encryption.md
    ├── 09-logging-monitoring.md
    ├── 10-secure-sdlc.md
    ├── 11-hr-security.md
    ├── 12-physical-security.md
    ├── 13-risk-management.md
    └── 14-asset-management.md
```

## What's already evidenced (continuous)

| TSC | Control | Live evidence source |
|---|---|---|
| CC6.1 | Logical access | Cognito User Pool `sa-east-1_15uR6sR9o`, audit log (`/tenant/audit`) |
| CC6.6 | TLS in transit | ALB ACM cert (auto-renew), Caddy TLS, HSTS headers |
| CC6.7 | Encryption at rest | RDS KMS, S3 SSE-KMS, EBS KMS, SSM SecureString |
| CC7.2 | System monitoring | Prometheus + Grafana + Alertmanager + 13 alert rules |
| CC7.3 | Incident detection | PagerDuty (critical) + Slack (warning) |
| CC8.1 | Change management | GitHub PR workflow + 16 hardened CI workflows |
| A1.2 | Backup & restore | Nightly Postgres + Grafana → S3, weekly **verified restore drill** |
| A1.3 | Capacity monitoring | ECS service metrics, CloudWatch alarms, queue depth alerts |
| C1.1 | Confidentiality of customer data | Tenant isolation by `tenant_id`, IDOR mitigations |
| PI1.1 | Processing integrity | Audit log on every tenant action, idempotent webhooks |
| P2.1 | LGPD privacy notice | `docs.telecomtowerpower.com.br/legal/privacy/` |

The remaining controls are evidenced through point-in-time artifacts (training records, vendor reviews, board minutes) collected on the cadence stated in each control row.

## Auditor logistics

- **Auditor portal access:** read-only IAM role `soc2-auditor` (issued just-in-time for the engagement); CloudWatch Logs Insights pre-saved queries for each control test.
- **Sample window:** Type II = 90+ days. Continuous controls produce ≥1 sample/day automatically.
- **Sampling guidance:** for population-based tests (e.g., access reviews, change tickets), auditor selects N=25 per AICPA AT-C 205.A22 unless population is smaller.
- **Walkthrough cadence:** 1 day kickoff + 1 week document review + remote testing thereafter; estimated 4 weeks engagement total.

## Out of scope

- The `git-credential-manager` repository in this workspace is unrelated upstream OSS — not part of the audit boundary.
- ML inference (SageMaker `coverage_predict` endpoint) processes only public ANATEL/SRTM data; no customer PII flows through it.
- Marketing site (`telecomtowerpower.com.br`/Landing) is static; no customer data is processed there.

## Contact

- **Compliance Owner:** CTO
- **Security incidents:** `security@telecomtowerpower.com.br`
- **Audit logistics:** `compliance@telecomtowerpower.com.br`
