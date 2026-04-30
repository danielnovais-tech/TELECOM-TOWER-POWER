# System Description — TELECOM TOWER POWER

> AICPA SOC 2 §3 narrative describing the system in scope.

## 1. Services provided

TELECOM TOWER POWER is a B2B SaaS platform providing radio-frequency (RF) link planning APIs for the Brazilian telecommunications market. The platform aggregates 140,498 cell-tower records (ANATEL + OpenCelliD), 90-meter SRTM terrain data, and proprietary multi-hop repeater algorithms behind a tiered REST API. Customers — primarily ISPs, RF consultants, and tower operators — query the API to qualify new connections, plan point-to-point links, generate technical PDFs, and visualize coverage heatmaps.

## 2. Principal service commitments and system requirements

### Service commitments

- **Availability:** 99.9% (Enterprise) / 99.95% (Ultra) measured monthly.
- **Confidentiality:** Customer data isolated by `tenant_id`; never shared with other tenants or sub-processors except as listed in the public sub-processor index.
- **Privacy:** LGPD-compliant; data subject access requests honored within 30 days.
- **Processing integrity:** Every tenant action audit-logged and queryable; PDF outputs deterministic and checksummed.
- **Security:** API keys + SSO/OIDC authentication; OWASP Top-10 mitigations; encrypted at rest and in transit.

### System requirements

- TLS 1.2+ for all client connections.
- Customers responsible for securing their own API keys and SSO credentials.
- Customers acknowledge data classification (public ANATEL data, no PII processed by core RF endpoints).

## 3. Components of the system

### Infrastructure

- **AWS sa-east-1 (São Paulo):** Primary region. ECS Fargate for the API, RDS Postgres for the database, S3 for backups and PDF storage, SQS for batch jobs, Lambda for batch processing, CloudWatch for logs, ALB for ingress.
- **EC2 (Docker Compose, sa-east-1):** Hosts frontend, Grafana, Prometheus, Alertmanager, Caddy reverse proxy.
- **Railway (separate provider):** Warm failover for the API tier; activated via Route 53 health-check failover.
- **AWS Cognito (sa-east-1):** User pool for SSO/OIDC with optional SAML federation.
- **Stripe:** Billing and subscription management.

### Software

- **Backend:** Python 3.10 + FastAPI (`telecom_tower_power_api.py`) + SQLAlchemy + Alembic.
- **Frontend:** React + Leaflet (single-page app) and Streamlit playground.
- **Workers:** AWS Lambda for batch (`batch_worker.py`) and priority batch (`worker.py`).
- **Observability:** Prometheus + Grafana + Alertmanager + OpenTelemetry traces.

### People

- Engineering (FastAPI + React).
- DevOps/SRE (AWS infra, GitHub Actions, observability).
- Security (least-privilege IAM, vulnerability mgmt, incident response).
- Customer Success (Ultra-tier dedicated CSM).
- Legal & Compliance (LGPD/GDPR, contracts, sub-processor management).

### Procedures

Documented in [`docs-site/docs/operations/runbook.md`](../../operations/runbook.md) and the policy set in [`policies/`](policies/).

### Data

- **Customer data:** company name, billing email, Stripe customer ID, API usage, audit log.
- **Public data:** ANATEL tower registry (CC-BY ANATEL), OpenCelliD (CC-BY-SA), SRTM (USGS public domain).
- **No PII** is collected by the RF analysis endpoints — input is geographic coordinates + technical parameters.
- **Privacy-relevant PII** is limited to billing email + (optionally) end-user names for Ultra-tier white-label and SSO users; processed under LGPD as legitimate interest + contract performance.

## 4. Boundaries of the system

In scope:

- All endpoints under `api.telecomtowerpower.com.br`.
- All AWS resources in account `490083271496`, region `sa-east-1`.
- The 19 GitHub Actions workflows under `.github/workflows/`.
- Cognito User Pool `sa-east-1_15uR6sR9o`.
- Stripe webhook endpoint at `app.telecomtowerpower.com.br/webhook`.
- Public docs at `docs.telecomtowerpower.com.br`.
- React frontend at `app.telecomtowerpower.com.br` and apex `telecomtowerpower.com.br`.

Out of scope:

- Upstream SaaS providers (AWS, Stripe, GitHub, PagerDuty, Slack, Cognito-as-a-managed-service) — covered by their respective SOC 2 reports.
- The MkDocs and React build pipelines are in scope; the resulting static assets are CDN-cached but the build workflow itself is the controlled artifact.
- Public ANATEL/OpenCelliD/SRTM ingestion pipelines (`load_anatel.py`, etc.) — input data is public; the pipelines are in scope for Processing Integrity but no Confidentiality risk.

## 5. Sub-service organizations

The following sub-processors are carved-out (in CC9.2 scope) — auditor relies on each provider's own SOC 2 report.

| Sub-processor | Service | SOC report relied upon |
|---|---|---|
| Amazon Web Services | Hosting (EC2, ECS, RDS, S3, Lambda, Cognito, ACM, KMS, etc.) | AWS SOC 2 Type II (latest) |
| Stripe | Billing | Stripe SOC 2 Type II |
| GitHub | Source control + CI/CD | GitHub SOC 2 Type II |
| PagerDuty | Critical alerting | PagerDuty SOC 2 Type II |
| Slack | Internal communication | Slack SOC 2 Type II |
| Railway | Failover hosting | Railway SOC 2 Type II (or carve-out narrative if unavailable) |
| Cloudflare (DNS via Route 53; CDN at docs) | DNS + CDN | AWS Route 53 (SOC 2) + Cloudflare SOC 2 |

Public sub-processor list: `docs.telecomtowerpower.com.br/legal/sub-processors/`.

## 6. Complementary subservice organization controls (CSOCs)

Customers' security controls that complement ours:

- API keys must be stored securely by the customer (AWS Secrets Manager, HashiCorp Vault, etc.).
- SSO IdP (where applicable) must enforce MFA, password policy, and timely de-provisioning.
- Customers must keep their billing/notification email up to date.

## 7. Significant changes during the period

To be filled in per audit period. Examples that would be material:

- Adoption of a new sub-processor.
- Major architectural change (e.g., new region, new DB engine).
- Tier addition (e.g., Ultra tier — added 2026-Q2).
