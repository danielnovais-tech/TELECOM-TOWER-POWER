# 05 — Vendor (Sub-processor) Management Policy

| | |
|---|---|
| **Owner** | Security + Legal |
| **Review cadence** | Annual per vendor |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC9.2 |

## 1. Principle

Every third party with access to customer data, or operating in the production critical path, undergoes due-diligence at onboarding and annually thereafter.

## 2. Vendor categorization

| Tier | Definition | Required reviews |
|---|---|---|
| **Critical** | Vendor processes customer data OR vendor outage causes >30 min of downtime | SOC 2 Type II + DPA + annual review + named contact |
| **Important** | Vendor processes only metadata OR outage causes degraded service | SOC 2 Type I or II + annual review |
| **Routine** | Internal tooling; no customer data; outage causes only internal friction | Initial security questionnaire only |

## 3. Current sub-processor list

| Vendor | Tier | Service | DPA | SOC 2 |
|---|---|---|---|---|
| Amazon Web Services | Critical | Hosting (compute, storage, DB, IAM, KMS) | Yes (AWS Customer Agreement DPA) | Type II ✓ |
| Stripe | Critical | Billing & payment | Yes | Type II ✓ |
| Cognito (AWS) | Critical | Auth/SSO | (covered by AWS DPA) | (covered) |
| GitHub | Critical | Source control + CI/CD | Yes | Type II ✓ |
| Railway | Important | Failover hosting | Yes | Type II / type I |
| PagerDuty | Important | Critical alerting | Yes | Type II ✓ |
| Slack | Important | Internal comms | Yes | Type II ✓ |
| Cloudflare | Important | DNS + CDN | Yes | Type II ✓ |
| Sentry (if used) | Important | Error monitoring | Yes | Type II ✓ |
| OpenAI / Bedrock | Routine | AI inference (no PII) | Bedrock: AWS DPA / OpenAI: data processing addendum | Bedrock: covered / OpenAI: SOC 2 |

Public list maintained at `docs.telecomtowerpower.com.br/legal/sub-processors/` per LGPD/GDPR transparency requirements.

## 4. Onboarding due diligence

Before granting a new vendor access to customer data or critical-path operations:

1. **Security questionnaire** — typically SIG-Lite or vendor's own SOC 2.
2. **DPA executed** — covering LGPD + GDPR + scope of processing.
3. **Risk assessment** — added to the risk register.
4. **Approval** — CTO sign-off recorded in `evidence/vendors/<vendor>/onboarding.pdf`.

## 5. Annual review

For Critical and Important vendors:

- Refresh of SOC report (current within 12 months).
- Review of DPA validity and any sub-sub-processor changes.
- Confirm named contact still valid.
- Document any incidents or material changes since last review.
- Re-score risk in the register.

Output: `evidence/vendors/<vendor>/YYYY-review.pdf`.

## 6. Termination

When ending a vendor relationship:

- Confirm vendor deletion of customer data (request written confirmation).
- Revoke API keys, IAM users, integration tokens.
- Update sub-processor list and notify customers ≥30 days in advance per Ultra contract.

## 7. Sub-processor changes

Material additions or removals require:

- Customer notification ≥30 days before activation (per LGPD Art. 17 + GDPR Art. 28).
- Update of public sub-processor list.
- Update of this policy.
