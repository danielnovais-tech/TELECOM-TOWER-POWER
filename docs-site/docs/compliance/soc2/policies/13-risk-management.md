# 13 — Risk Management Policy

| | |
|---|---|
| **Owner** | CTO + Security |
| **Review cadence** | Annual; risk register quarterly |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC3.1, CC3.2, CC3.4, CC9.1 |

## 1. Methodology

Risk = Likelihood × Impact, each scored 1–5, giving a 1–25 risk score.

| Score | Treatment |
|---|---|
| ≤ 5 | Accept; document |
| 6–11 | Monitor; reassess quarterly |
| 12–17 | Mitigate; controlled within 90 days |
| 18–25 | Mitigate or transfer immediately; CTO sign-off required |

## 2. Risk register

The risk register lives in `evidence/risk/risk-register-YYYY.xlsx` (or equivalent). Each entry includes:

- ID
- Description
- Category (Strategic, Operational, Compliance, Financial, Cyber, Privacy)
- Likelihood (1–5)
- Impact (1–5)
- Inherent score
- Mitigations
- Residual score
- Owner
- Review date
- Status

## 3. Cadence

- **Quarterly:** Security lead reviews open risks, updates likelihood/impact, presents to CTO.
- **Annual:** Full register refresh; new risks identified via threat modelling and incident retrospectives; advisory board reviews top 5 risks.

## 4. Top risks (2026 cycle)

| Risk | L | I | Score | Mitigation | Residual |
|---|---|---|---|---|---|
| Backup corruption discovered too late | 3 | 5 | 15 | Weekly verified restore drill | 4 |
| Single-region outage (sa-east-1) | 2 | 5 | 10 | Railway warm failover + Route 53 health check | 4 |
| Sub-processor compromise (Stripe / Cognito) | 2 | 4 | 8 | Vendor reviews + DPAs + monitoring | 6 |
| API key leak by customer | 4 | 3 | 12 | Hashing at rest + per-tenant rate limits + customer-rotatable keys | 6 |
| Insider threat (privileged access) | 2 | 5 | 10 | MFA + quarterly access review + audit log + break-glass with approval | 4 |
| Dependency CVE exploited before patch SLA | 3 | 4 | 12 | Dependabot + ECR scan + 7-day SLA on critical | 6 |
| LGPD/GDPR DSAR not handled in time | 2 | 4 | 8 | 30-day SLA + ticket workflow + Legal review | 4 |
| DDoS overwhelming ALB | 3 | 3 | 9 | AWS Shield Standard + Cloudflare WAF (where applicable) + autoscaling | 6 |
| Loss of CTO / single-person dependency | 2 | 5 | 10 | Documented runbooks; deputy designated for each critical role | 6 |
| Accidental destructive migration | 2 | 5 | 10 | Reviewer + 2-phase rollout + verified backups | 4 |

## 5. Risk treatment

- **Avoid** — change scope to remove the risk source.
- **Mitigate** — implement controls (preferred path).
- **Transfer** — insurance / contractual indemnity.
- **Accept** — document with CTO sign-off and review date.

## 6. Communication

- High-residual risks (≥ 12 after mitigation) reported to advisory board quarterly.
- Material new risks (e.g., new sub-processor) communicated within the same quarter.

## 7. Insurance

- Cyber liability insurance retained at level appropriate to current revenue + customer-data volume; reviewed annually.
- Policy details: `evidence/insurance/cyber-policy-YYYY.pdf`.
