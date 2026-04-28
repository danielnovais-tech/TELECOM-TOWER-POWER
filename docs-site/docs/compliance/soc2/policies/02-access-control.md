# 02 — Access Control Policy

| | |
|---|---|
| **Owner** | Security |
| **Review cadence** | Annual; access reviews quarterly |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC6.1, CC6.2, CC6.3, CC6.4 |

## 1. Principles

- **Least privilege:** Users and services receive the minimum access required for their function.
- **Need to know:** Customer data access is restricted to roles requiring it.
- **Separation of duties:** No single individual can both deploy a change and approve it.

## 2. Identity sources

- **Workforce:** AWS IAM Identity Center federated to corporate IdP (Cognito + optional SAML).
- **Customers:** Per-tenant API keys (X-API-Key header) and/or Cognito Bearer tokens (SSO).
- **Service principals:** AWS IAM roles with task-specific scopes; no long-lived access keys in code.

## 3. Provisioning

- All workforce accounts created within 1 business day of HR approval.
- All API keys issued via either (a) Stripe webhook on successful subscription, or (b) admin-provisioned with audit log entry.
- Default access on creation: read-only. Elevation requires a ticket signed by the user's manager.

## 4. Authentication

- **Workforce:** MFA mandatory (TOTP or WebAuthn). Password policy: 14+ chars, no rotation unless compromise suspected (NIST SP 800-63B).
- **Customers:** API keys are 32+ random bytes (URL-safe base64). SSO ID tokens validated for `iss`, `aud`, `exp`, `sub`, `token_use=id`, RS256/RS384/RS512.
- **Service:** AWS IAM role assumption only; no static credentials.

## 5. Authorization

- Role-based access via Cognito groups: `admin`, `engineer`, `support`, `read-only`, `auditor`.
- API tier enforces feature scope (`require_tier(...)` decorator). Ultra is a superset of Enterprise.
- Tenant data is filtered by `tenant_id` in every query. IDOR test suite runs in CI.

## 6. Periodic access review

- **Quarterly:** Security lead exports all human + service principals + their effective permissions, presents to CTO. CTO approves or revokes per principal. Output stored in `evidence/access-reviews/YYYY-Q*.pdf`.
- **Continuous:** AWS Access Analyzer + GuardDuty alert on anomalous access patterns.

## 7. Termination

- Workforce: All access revoked within 1 business day of termination. Cognito user disabled; AWS SSO session terminated; GitHub access removed; Slack deactivated; PagerDuty user removed. Evidence: `evidence/hr/offboarding/<user>.json`.
- Customer: API keys revoked within 30 days of contract end (or immediately on customer request).

## 8. Privileged access

- AWS root account credentials in physical safe; used only for billing/account-level changes.
- Production database write access limited to migration-runner IAM role; humans use read-only RDS proxy.
- `break-glass` access for production: pre-issued IAM role assumable only with MFA + Slack-approved request, auto-expires in 4 hours.

## 9. Sanctions

Unauthorized access attempts are investigated by the Security lead and may result in account suspension, disciplinary action, or referral to law enforcement.
