# 12 — Physical Security Policy

| | |
|---|---|
| **Owner** | Security |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC6.5 |

## 1. Production data centers

TELECOM TOWER POWER operates **no on-premises infrastructure**. All production workloads run in AWS data centers in São Paulo (`sa-east-1`). Physical security is inherited from AWS and evidenced by their SOC 2 Type II report (auditor relies on AWS attestation per CC9.2 carve-out).

## 2. Workforce devices

- All workforce devices are corporate-managed via MDM (full-disk encryption enforced, screen lock ≤ 5 min, OS patches automated).
- Personal devices are not permitted access to production data or systems.
- Lost or stolen devices: report immediately to `security@`. MDM remote wipe within 1 hour of report. Incident logged.

## 3. Office security

The company is remote-first; there is no permanent office. When a temporary office or co-working space is used:

- Visitor access is escorted.
- Devices are not left unattended.
- Confidential conversations happen in private rooms.

## 4. Travel

- Production access while traveling allowed only over corporate VPN / zero-trust gateway.
- Devices set to require MFA on every wake; never left unattended.
- High-risk jurisdictions (per Legal) require pre-travel approval and possibly a clean travel laptop.

## 5. Disposal

- End-of-life devices are wiped per NIST SP 800-88 (cryptographic erase or physical destruction for SSDs).
- Disposal certificate retained for 3 years.
