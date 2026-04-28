# 07 — Data Classification & Handling Policy

| | |
|---|---|
| **Owner** | Security + Legal |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | C1.1, C1.2, P3.1, P4.1 |

## 1. Classification levels

| Level | Definition | Examples | Handling |
|---|---|---|---|
| **Public** | Intended for free distribution | Marketing site, public docs, ANATEL/OpenCelliD/SRTM tower data | No restrictions |
| **Internal** | Routine business info; not for external distribution | Internal runbooks, SLO targets, postmortem drafts | Workforce only; standard tooling |
| **Confidential** | Customer data; business sensitive | Customer email, API keys (hashed), audit log entries, billing records, Stripe customer IDs | Encrypted at rest + in transit; access only on need-to-know; audit-logged |
| **Restricted** | Highly sensitive; regulatory or contractual | Cognito JWT signing keys, AWS KMS keys, Stripe webhook secret, PagerDuty routing key, employee PII | Stored in AWS SSM SecureString or KMS; access requires MFA + ticket; access logged via CloudTrail |

## 2. Labelling

- DB tables containing Confidential/Restricted data are documented in `models.py` with a `# CLASS: confidential` or `# CLASS: restricted` comment on the class.
- S3 buckets are labelled via tag `data-classification: <level>`.
- Files in `evidence/` follow the same tagging.

## 3. Encryption

- **At rest:** All Confidential and Restricted data encrypted via AWS KMS (RDS, EBS, S3, SSM SecureString).
- **In transit:** TLS 1.2+ enforced. See [`08-encryption.md`](08-encryption.md) for cipher suite and key-management detail.

## 4. Retention

| Class | Default retention |
|---|---|
| Public | Indefinite |
| Internal | 5 years (then archive or delete per relevance) |
| Confidential — billing & audit | 5 years (LGPD/IRS-equivalent) |
| Confidential — customer payload | Term of contract + 30 days |
| Restricted — secrets | Until rotation; old versions deleted within 90 days |

## 5. Disposal

- Customer payload data deleted (cryptographic erasure or DB purge) within 30 days of contract termination, with a deletion certificate available on request.
- Backups age out per S3 lifecycle (Glacier Deep Archive then expire).
- Workforce devices wiped with vendor-validated procedure on offboarding.

## 6. Customer obligations (CSOCs)

Customers acknowledge in their agreement that:

- API keys are Restricted data and must be stored in a secrets manager.
- They will not include third-party PII in `name` or `notes` fields beyond what's needed for the RF analysis.
- They are responsible for LGPD/GDPR obligations toward their own end-users for any data they ingest.

## 7. PII inventory

- **Collected by TELECOM TOWER POWER:** billing email, company name, billing address, tier, Stripe customer ID, IP address (audit log), Cognito user `sub` (for SSO users).
- **Not collected:** national ID numbers, payment card data (handled exclusively by Stripe), biometric data, location data of end-users.
- **Inferred from inputs:** geographic coordinates of receiver locations (treated as Confidential per data-min principle).
