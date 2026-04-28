# 11 — HR Security Policy

| | |
|---|---|
| **Owner** | HR (with Security) |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC1.1, CC1.4, CC1.5, CC6.3 |

## 1. Pre-employment

- **Background checks** for all roles with production access. Scope: identity, employment history, criminal record (local jurisdiction), where legally permitted. Adverse findings reviewed by CTO + Legal before hire.
- **References** for senior or security-sensitive roles.
- **Offer & contract** include confidentiality obligations and acknowledgement of policies.

## 2. Onboarding

Within 5 business days of start date:

- Code of conduct signed.
- Acceptable Use annex acknowledged.
- Security awareness training completed.
- Account provisioning (workforce IdP, Cognito group, AWS SSO, GitHub team, Slack workspace, PagerDuty rotation if applicable).
- Hardware delivered with disk encryption + MDM.

## 3. During employment

- **Annual security training** — KnowBe4 (or equivalent) phishing + secure-coding for engineers.
- **Performance reviews** include security-aligned objectives where role-relevant.
- **Awareness campaigns** — quarterly Slack post on a current threat (phishing, credential reuse, etc.).

## 4. Role changes

When an employee changes role:

- Manager files request to revoke old privileges and grant new.
- Security lead approves elevated access (admin, prod, etc.).
- Update logged in `evidence/hr/role-changes/`.

## 5. Offboarding

Within 1 business day of termination effective time:

- All system access revoked (workforce IdP, AWS, GitHub, Slack, PagerDuty, etc.).
- Hardware returned (or remote wipe via MDM).
- Outstanding code-review responsibilities transferred.
- Exit interview by HR; security-relevant feedback captured.

Evidence: `evidence/hr/offboarding/<user>.json` with timestamps for each revocation step.

## 6. Disciplinary process

- Suspected violations of [`01-information-security.md`](01-information-security.md) trigger HR + Security review.
- Confirmed violations may result in retraining, written warning, suspension, termination, or referral to law enforcement.

## 7. Contractors & temps

- Same controls as employees: BG check (proportional to access), training, offboarding.
- Default access is read-only and time-bounded; renewals require manager + Security approval.
