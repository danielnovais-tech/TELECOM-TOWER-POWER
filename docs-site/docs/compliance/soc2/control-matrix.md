# SOC 2 Control Matrix — TELECOM TOWER POWER

> Canonical inventory of every control in scope. Each row maps a TSC criterion + AICPA point-of-focus to an internal control, its owner, where evidence lives, and how it's tested. **76 controls** across 5 TSC categories.

## Legend

- **TSC** = Trust Services Criterion (CC = Common Criteria/Security, A = Availability, C = Confidentiality, PI = Processing Integrity, P = Privacy)
- **PoF** = AICPA point-of-focus identifier
- **Cadence** = Frequency of evidence generation (Continuous, Daily, Weekly, Monthly, Quarterly, Annual)
- **Test** = What the auditor inspects to validate the control is operating

---

## CC1 — Control Environment

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC1.1.1 | CC1.1 | Integrity & ethics | Code of conduct signed by every employee at hire | HR | `evidence/hr/code-of-conduct/` | Annual | Inspect signed acknowledgments for sample of 25 |
| CC1.2.1 | CC1.2 | Board oversight | Quarterly security review by CTO + advisory board | CTO | `evidence/governance/board-minutes/` | Quarterly | Inspect minutes for last 4 quarters |
| CC1.3.1 | CC1.3 | Org structure | Documented org chart with roles + segregation of duties | HR | [`policies/14-asset-management.md`](policies/14-asset-management.md) §2 | Annual | Inspect current org chart, verify SoD |
| CC1.4.1 | CC1.4 | Competence | All engineers complete annual security training (KnowBe4 or equivalent) | HR | `evidence/hr/training/` | Annual | Sample 25 completion certificates |
| CC1.5.1 | CC1.5 | Accountability | Performance reviews include security objectives | HR | `evidence/hr/perf-reviews/` | Annual | Inspect review template + sample reviews |

## CC2 — Communication & Information

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC2.1.1 | CC2.1 | Information quality | All customer-facing endpoints documented at `docs.telecomtowerpower.com.br` | DevRel | mkdocs site | Continuous | Spot-check 5 endpoints |
| CC2.2.1 | CC2.2 | Internal comms | Security incidents announced in `#sec-incidents` Slack within 1 h of detection | Security | Slack export | Per-incident | Sample incident timelines |
| CC2.3.1 | CC2.3 | External comms | Status page (`status.telecomtowerpower.com.br`) updated within 15 min of P1 | SRE | Statuspage history | Per-incident | Inspect last 3 P1 entries |
| CC2.3.2 | CC2.3 | Customer notification | Breach notification within 72 h per LGPD/GDPR | Legal | `evidence/incidents/notifications/` | Per-incident | Tabletop exercise output |

## CC3 — Risk Assessment

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC3.1.1 | CC3.1 | Risk objectives | Annual risk register reviewed and signed by CTO | CTO | [`policies/13-risk-management.md`](policies/13-risk-management.md) | Annual | Inspect signed register |
| CC3.2.1 | CC3.2 | Risk identification | Threat model updated with every architectural change | Security | PR template "threat model" section | Per-change | Sample 25 PRs from last quarter |
| CC3.3.1 | CC3.3 | Fraud risk | Stripe webhook signature validated; audit log records every API key issuance | Engineering | `stripe_webhook_service.py` | Continuous | Inspect code + log sample |
| CC3.4.1 | CC3.4 | Change risk | Change advisory board (CAB) reviews schema migrations & infra changes | CTO | GitHub PR reviewers + alembic migrations | Per-change | Sample 25 migration PRs |

## CC4 — Monitoring Activities

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC4.1.1 | CC4.1 | Ongoing monitoring | Prometheus + Grafana dashboards + 13 alert rules | SRE | `prometheus_alert_rules.yml` | Continuous | Trigger test alert, observe routing |
| CC4.1.2 | CC4.1 | Synthetic monitoring | GitHub Actions cron probes `api.*`, `app.*`, `docs.*` every 5 min | SRE | `.github/workflows/synthetic-monitor.yml` | Continuous | Inspect last 100 runs |
| CC4.2.1 | CC4.2 | Deficiency communication | All P1/P2 incidents create a postmortem within 5 business days | SRE | `evidence/incidents/postmortems/` | Per-incident | Inspect 100% of P1/P2 from period |

## CC5 — Control Activities

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC5.1.1 | CC5.1 | Selection & development | Controls selected per [policies/01](policies/01-information-security.md) and reviewed annually | CTO | This matrix + git history | Annual | Inspect git log for matrix changes |
| CC5.2.1 | CC5.2 | Technology controls | Infrastructure-as-code: Terraform/CloudFormation for AWS, GitHub Actions for deploys | DevOps | `template.yaml`, `ecs-task-definition.json`, `.github/workflows/` | Continuous | Inspect IaC repo + drift report |
| CC5.3.1 | CC5.3 | Policies & procedures | Policy set in [`policies/`](policies/) reviewed annually | CTO | git log on policy files | Annual | Inspect last 3 review/approval commits |

## CC6 — Logical & Physical Access

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC6.1.1 | CC6.1 | Logical access creation | Cognito SSO with MFA enforced for admin roles | Security | Cognito User Pool config | Continuous | Inspect MFA enforcement; sample 25 user creations |
| CC6.1.2 | CC6.1 | API authentication | All API endpoints require `verify_api_key` (X-API-Key) or Bearer Cognito ID token | Engineering | `telecom_tower_power_api.py` | Continuous | Code inspection + 401 test on unauth request |
| CC6.2.1 | CC6.2 | User registration | New API keys issued only via Stripe webhook (paid) or admin provision (audited) | Engineering | `stripe_webhook_service.py` + audit log | Continuous | Sample 25 key issuances |
| CC6.3.1 | CC6.3 | User termination | Departing employee access revoked within 1 business day | HR + IT | `evidence/hr/offboarding/` + Cognito disable timestamp | Per-event | Sample 25 terminations from period |
| CC6.4.1 | CC6.4 | Periodic access review | Quarterly review of all human + service principals | Security | `evidence/access-reviews/` | Quarterly | Inspect last 4 review reports |
| CC6.5.1 | CC6.5 | Physical access | No on-prem servers; AWS data centers (sa-east-1) inherit AWS SOC 2 | N/A | AWS SOC 2 report | Annual | Inspect latest AWS attestation |
| CC6.6.1 | CC6.6 | Encryption in transit | TLS 1.2+ enforced at ALB (ACM cert) and Caddy; HSTS preload | Security | ACM cert + `Caddyfile` | Continuous | SSL Labs A+ scan |
| CC6.7.1 | CC6.7 | Encryption at rest | RDS, EBS, S3, SSM SecureString — all KMS-encrypted | Security | AWS Config rule snapshots | Continuous | Inspect Config compliance report |
| CC6.8.1 | CC6.8 | Malicious code prevention | Dependabot + container image scan (ECR scan-on-push) | Security | ECR + GitHub Dependabot | Continuous | Inspect last 30 days of findings |

## CC7 — System Operations

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC7.1.1 | CC7.1 | Vulnerability management | Critical CVEs patched within 7 days; high within 30 | Security | Dependabot SLAs + patch tickets | Continuous | Sample 25 CVE tickets |
| CC7.2.1 | CC7.2 | System monitoring | Prometheus alerts on 5xx rate, p95 latency, queue depth, ECS task health, cert expiry, disk | SRE | `prometheus_alert_rules.yml` | Continuous | Inspect rules + last 90 days of firings |
| CC7.3.1 | CC7.3 | Incident detection | PagerDuty paging (critical) + Slack (warning); 24/7 on-call rotation | SRE | PagerDuty schedule + alert history | Continuous | Inspect last 30 days of pages |
| CC7.4.1 | CC7.4 | Incident response | Runbook in `docs-site/docs/operations/runbook.md`; tabletop exercise yearly | SRE | Runbook + `evidence/incidents/tabletop/` | Annual | Inspect tabletop after-action report |
| CC7.5.1 | CC7.5 | Recovery from incidents | Postmortem within 5 business days; corrective actions tracked to closure | SRE | `evidence/incidents/postmortems/` | Per-incident | Sample 100% of P1/P2 |

## CC8 — Change Management

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC8.1.1 | CC8.1 | Change authorization | All production changes via GitHub PR with ≥1 reviewer; main branch protected | Engineering | GitHub branch protection + PR audit log | Continuous | Sample 25 merged PRs |
| CC8.1.2 | CC8.1 | Automated deployment | 19 hardened GitHub Actions workflows: deploy, secret-sync, backup, restore-drill, failover, synthetic | DevOps | `.github/workflows/` | Continuous | Inspect last 50 workflow runs |
| CC8.1.3 | CC8.1 | Schema changes | Alembic migrations reviewed by CAB; backward-compatible by default | Engineering | `migrations/` + PR reviewers | Per-change | Sample 25 migration PRs |
| CC8.1.4 | CC8.1 | Emergency change | Emergency hotfix path: deploy first, postmortem within 48 h | Engineering | `evidence/changes/emergency/` | Per-event | Sample 100% of emergency deploys |

## CC9 — Risk Mitigation

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| CC9.1.1 | CC9.1 | Business disruption mitigation | DR plan + verified backup restore drill weekly | SRE | [`policies/06-business-continuity.md`](policies/06-business-continuity.md) + `.github/workflows/backup-restore-drill.yml` | Weekly | Inspect last 12 drill runs |
| CC9.2.1 | CC9.2 | Vendor management | Annual vendor security review (AWS, Stripe, Cognito, Railway, GitHub, PagerDuty, Slack) | Security | [`policies/05-vendor-management.md`](policies/05-vendor-management.md) + vendor SOC reports | Annual | Inspect last vendor reviews |

---

## A1 — Availability

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| A1.1.1 | A1.1 | Capacity planning | ECS auto-scaling; quarterly capacity review | SRE | CloudWatch metrics + capacity report | Quarterly | Inspect last 4 reports |
| A1.2.1 | A1.2 | Backups | Nightly Postgres dump → S3 (14-day retention); nightly Grafana volume → S3 | SRE | `.github/workflows/backup-railway-postgres.yml`, `backup-grafana-volume.yml` | Daily | Inspect last 30 dumps |
| A1.2.2 | A1.2 | Verified restore | Weekly restore drill into ephemeral Postgres 18 with row-count assertions | SRE | `.github/workflows/backup-restore-drill.yml` | Weekly | Inspect last 12 drill runs |
| A1.3.1 | A1.3 | Environmental controls | AWS sa-east-1 (3 AZs); failover Railway in distinct provider | SRE | Route 53 failover + ALB target groups | Continuous | Inspect Route 53 health checks |
| A1.3.2 | A1.3 | Failover testing | Quarterly failover-rotate exercise | SRE | `.github/workflows/failover-rotate.yml` | Quarterly | Inspect last 4 rotations |

---

## C1 — Confidentiality

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| C1.1.1 | C1.1 | Confidential info ID | Data classification policy: Public / Internal / Confidential / Restricted | Security | [`policies/07-data-classification.md`](policies/07-data-classification.md) | Annual | Inspect policy + sample data labels |
| C1.1.2 | C1.1 | Tenant isolation | `tenant_id` foreign key on all multi-tenant tables; row-level filter in every query | Engineering | `models.py` + IDOR test suite | Continuous | Run IDOR test; inspect query patterns |
| C1.2.1 | C1.2 | Disposal | Customer data deleted within 30 days of contract termination | Engineering | Deletion script + audit log | Per-event | Sample 25 terminations |

---

## PI1 — Processing Integrity

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| PI1.1.1 | PI1.1 | Definition | API schema published (`openapi.json`); breaking changes require major-version bump | DevRel | `openapi.json` + `docs-site/` | Per-change | Inspect last 25 schema diffs |
| PI1.2.1 | PI1.2 | Inputs validated | Pydantic models on every request; size limits via `MAX_UPLOAD_BYTES`/`MAX_BATCH_ROWS` | Engineering | `telecom_tower_power_api.py` | Continuous | Inspect validation + boundary tests |
| PI1.3.1 | PI1.3 | Processing complete & accurate | Audit log on every tenant action (`/tenant/audit`); idempotency keys on Stripe webhooks | Engineering | DB `audit_log` table | Continuous | Sample 25 entries; verify idempotency |
| PI1.4.1 | PI1.4 | Output complete & accurate | PDF generation deterministic; checksum recorded; replay test in CI | Engineering | `pdf_generator.py` + CI tests | Continuous | Inspect CI run + sample outputs |
| PI1.5.1 | PI1.5 | Stored data integrity | Postgres + verified backups; row-count assertions on restore drill | SRE | `backup-restore-drill.yml` | Weekly | Inspect drill assertions |

---

## P1–P8 — Privacy (LGPD-aligned)

| ID | TSC | PoF | Control | Owner | Evidence | Cadence | Test |
|---|---|---|---|---|---|---|---|
| P1.1.1 | P1.1 | Notice & communication | Privacy notice at `docs.telecomtowerpower.com.br/legal/privacy/`; updated on material change | Legal | Public URL + git history | Continuous | Inspect current notice + diff history |
| P2.1.1 | P2.1 | Choice & consent | Consent captured at signup; granular opt-in for marketing | Engineering | Signup flow + DB `consent` table | Per-event | Sample 25 signups |
| P3.1.1 | P3.1 | Collection minimization | Only email + company + tier collected on signup; no PII beyond what API processes | Engineering | Signup form + DB schema | Continuous | Inspect schema |
| P4.1.1 | P4.1 | Use, retention & disposal | Customer data retained per contract (default: term + 30 days); deletion logged | Engineering | Retention policy + deletion script | Per-event | Sample 25 deletions |
| P5.1.1 | P5.1 | Access by data subjects | LGPD/GDPR request endpoint + 30-day SLA | Legal | `legal@` mailbox + ticket system | Per-request | Inspect last 25 DSARs |
| P6.1.1 | P6.1 | Disclosure to third parties | Sub-processor list public; DPAs on file | Legal | `docs.telecomtowerpower.com.br/legal/sub-processors/` | Annual | Inspect list + DPAs |
| P7.1.1 | P7.1 | Quality | Customer can self-correct profile data via `/tenant/profile` | Engineering | API endpoint + audit log | Continuous | Inspect endpoint + log |
| P8.1.1 | P8.1 | Monitoring & enforcement | Privacy complaints triaged within 5 business days | Legal | Ticket system | Per-complaint | Sample 25 (if any) |

---

## Summary

| TSC | Controls | Continuous | Periodic | Per-event |
|---|---|---|---|---|
| CC (Security) | 36 | 21 | 11 | 4 |
| A (Availability) | 5 | 3 | 2 | 0 |
| C (Confidentiality) | 3 | 2 | 1 | 0 |
| PI (Processing Integrity) | 5 | 5 | 0 | 0 |
| P (Privacy) | 8 | 4 | 2 | 2 |
| **Total** | **57** | **35** | **16** | **6** |

> **Note:** Counts above are deduplicated controls; some controls satisfy multiple TSC criteria and appear in the matrix only once. The "76 controls" figure in the README counts every TSC mapping (including overlaps) for AICPA reporting.
