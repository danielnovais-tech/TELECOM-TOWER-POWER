# 01 — Information Security Policy

| | |
|---|---|
| **Owner** | CTO |
| **Review cadence** | Annual (or upon material change) |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Applies to** | All employees, contractors, and third parties processing TELECOM TOWER POWER data |

## 1. Purpose

Establish the high-level information-security objectives that govern every system, process, and person at TELECOM TOWER POWER. This policy is the apex document; all subsequent policies (Access Control, Encryption, etc.) implement specific aspects of this one.

## 2. Scope

All TELECOM TOWER POWER systems in the SOC 2 audit boundary defined in [`system-description.md`](../system-description.md), including production AWS account `490083271496/sa-east-1`, GitHub repositories, Cognito user pool, Stripe webhook integration, and the 19 production workflows.

## 3. Policy

### 3.1 Confidentiality, Integrity, Availability (CIA)

- **Confidentiality:** Customer data is isolated by `tenant_id`. Access requires authenticated API key or Cognito Bearer token. Encryption at rest (KMS) and in transit (TLS 1.2+) is mandatory for all customer data.
- **Integrity:** Every tenant-affecting action is recorded in the audit log (CC8.1, PI1.3). Production changes flow through pull requests with at least one reviewer (CC8.1.1).
- **Availability:** 99.9% (Enterprise) / 99.95% (Ultra) SLA, supported by ECS auto-scaling, Railway failover, nightly backups, and weekly verified restore drills.

### 3.2 Risk-based control selection

Controls are selected and prioritized based on the annual risk register (see [`13-risk-management.md`](13-risk-management.md)). Threats with likelihood × impact ≥ 12 (on a 5-point scale) require a documented mitigation in the control matrix.

### 3.3 Roles and responsibilities

| Role | Responsibility |
|---|---|
| CTO | Owner of this policy; approves all security exceptions |
| Security lead | Maintains threat model, runs vulnerability mgmt, owns incident response |
| Engineering | Implements technical controls, writes secure code per [`10-secure-sdlc.md`](10-secure-sdlc.md) |
| SRE | Operates production infrastructure, owns availability + monitoring |
| HR | Owns onboarding/offboarding, training, code-of-conduct attestations |
| Legal | Owns privacy notice, sub-processor management, DSAR handling |

### 3.4 Acceptable use

- All employees must follow the Acceptable Use Annex (signed at hire). Highlights:
  - No customer data on personal devices.
  - No production credentials in chat, email, or screenshots.
  - MFA required on every workforce account.
  - Production access only from corporate-managed devices over VPN or zero-trust gateway.

### 3.5 Exception handling

Any deviation from this or downstream policies requires a written exception, signed by the CTO, with an explicit expiry date and remediation plan. Exceptions live in `evidence/exceptions/` and are reviewed quarterly.

### 3.6 Sanctions

Violations may result in disciplinary action up to and including termination, and (where applicable) civil or criminal referral.

## 4. Review

This policy is reviewed annually by the CTO. The review event is committed to git on file `docs-site/docs/compliance/soc2/policies/01-information-security.md`. Material changes (e.g., new TSC scope) require advisory-board notification.

## 5. References

- AICPA Trust Services Criteria 2017 (revised 2022)
- ISO/IEC 27001:2022 Annex A
- LGPD Lei nº 13.709/2018 (Brazil)
