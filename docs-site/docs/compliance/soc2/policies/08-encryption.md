# 08 — Encryption & Key Management Policy

| | |
|---|---|
| **Owner** | Security |
| **Review cadence** | Annual |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC6.6, CC6.7 |

## 1. In transit

- **External (customer ↔ TELECOM TOWER POWER):** TLS 1.2 minimum (TLS 1.3 preferred). Modern cipher suites only (no RC4, 3DES, CBC with SHA-1). HSTS preload enabled with `includeSubDomains; preload`. Configured at AWS ALB (ACM certificate, auto-renewed) and Caddy.
- **Internal (service ↔ service in AWS):** TLS for ECS → RDS (`sslmode=require`), ALB → ECS task (HTTP within VPC accepted as low-risk; documented exception). All cross-account/cross-VPC traffic uses TLS or AWS PrivateLink.
- **Egress to sub-processors:** TLS to Stripe (sk_live_*), Cognito (HTTPS), PagerDuty, Slack.

## 2. At rest

| Resource | Encryption | Key |
|---|---|---|
| RDS Postgres | AWS KMS | Customer-managed CMK in sa-east-1 |
| EBS volumes | AWS KMS | Customer-managed CMK |
| S3 buckets | SSE-KMS | Customer-managed CMK |
| SSM Parameter Store | SecureString (KMS) | Customer-managed CMK |
| ECR images | KMS-managed | AWS-managed CMK (default) |
| CloudWatch Logs | KMS | Customer-managed CMK |
| Cognito user data | AWS-managed | (managed by Cognito) |

## 3. Key management

- **Hierarchy:** Customer-managed CMKs in AWS KMS for all Confidential/Restricted data (see [`07-data-classification.md`](07-data-classification.md)).
- **Rotation:** AWS KMS auto-rotation enabled (annual). Cognito JWT signing keys rotated annually.
- **Access:** KMS key policies restrict use to the specific IAM roles that need them (least privilege).
- **Backup:** AWS KMS keys are AWS-managed; loss is mitigated by AWS's own controls.
- **Deletion:** KMS keys are scheduled for 30-day deletion (max waiting period) before actual deletion.

## 4. Application secrets

- All application secrets (Stripe keys, PagerDuty routing keys, DB passwords, JWT signing keys) live in SSM Parameter Store SecureString.
- Synced to runtime via dedicated workflows (`update-ec2-stripe-secrets.yml`, `update-ec2-alerting-secrets.yml`) or ECS task-definition `secrets:` references.
- No secrets in environment files committed to git. `git-secrets` pre-commit hook (or equivalent) blocks commits containing `sk_live_`, `whsec_`, AWS access-key patterns.

## 5. Secret rotation

| Secret | Rotation cadence |
|---|---|
| Stripe live keys | Annual or on suspected compromise |
| PagerDuty routing key | Annual or on exposure (e.g., chat leak) |
| DB master password | Annual |
| Cognito JWT signing | Annual (auto, AWS-managed) |
| AWS IAM access keys | Avoided entirely; use IAM roles |
| API keys (customer) | Customer-controlled rotation via `/tenant/keys`; advised quarterly |

## 6. Cryptographic primitives

- **Hashing:** SHA-256 minimum. API keys hashed at rest with HMAC-SHA-256.
- **Symmetric:** AES-256 (KMS).
- **Asymmetric:** RSA 2048+ or ECDSA P-256+. SSO ID tokens accept RS256/RS384/RS512.
- **Random:** `os.urandom` / `secrets.token_*` in Python; never `random` module.

## 7. PCI-DSS scope

TELECOM TOWER POWER does **not** store, process, or transmit cardholder data. Stripe Checkout handles all PCI-scoped operations (SAQ-A applies). No cardholder data crosses our infrastructure.
