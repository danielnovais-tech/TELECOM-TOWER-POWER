# 14 — Asset Management Policy

| | |
|---|---|
| **Owner** | DevOps + IT |
| **Review cadence** | Annual; inventory monthly |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC1.3, CC2.1, CC5.2 |

## 1. Asset categories

| Category | Examples | Inventory source |
|---|---|---|
| Cloud resources | EC2, ECS, RDS, S3, Lambda, Cognito, ALB | AWS Config inventory, Terraform/CloudFormation state |
| Code | GitHub repositories | GitHub org listing |
| Workforce devices | Laptops, phones | MDM inventory |
| SaaS sub-processors | Stripe, Slack, PagerDuty, etc. | Vendor list ([`05-vendor-management.md`](05-vendor-management.md)) |
| Data assets | Postgres tables, S3 buckets, audit logs | Data classification inventory ([`07-data-classification.md`](07-data-classification.md)) |
| Domains & DNS | telecomtowerpower.com.br + subdomains | Route 53 / registrar console |
| Certificates | ACM certs, code-signing | ACM console |

## 2. Inventory cadence

- **Continuous:** AWS Config records all configuration changes; AWS Resource Explorer provides cross-region search.
- **Monthly:** DevOps reconciles Terraform/CloudFormation state vs actual; drift triggers a follow-up PR.
- **Annual:** Full asset reconciliation including SaaS subscriptions, domains, certificates.

## 3. Ownership

Every asset has a named owner (in tags `owner=<team-or-individual>` for AWS, in CODEOWNERS for code, in vendor sheet for SaaS). Unowned assets are flagged at the monthly reconciliation and assigned within 5 business days.

## 4. Acceptable use

Assets may be used only for legitimate business purposes. See [`01-information-security.md`](01-information-security.md) §3.4.

## 5. Org chart & SoD

Current organizational structure with separation-of-duties is maintained at `evidence/governance/org-chart.pdf`, refreshed at every hire or role change. Notable SoD boundaries:

- The engineer who writes a change cannot approve their own merge (GitHub branch protection).
- The deployer cannot approve their own emergency-change retrospective.
- The Security lead is independent from the Engineering lead.

## 6. Lifecycle

- **Provisioning:** Via IaC (preferred) or with documented justification in PR.
- **Operating:** Tagged with `owner`, `environment`, `data-classification`, `cost-center`.
- **Decommissioning:** All decommissioned resources go through a documented checklist (revoke IAM, delete S3 / EBS / RDS, update DNS, archive logs); evidence stored in `evidence/decommissions/`.
