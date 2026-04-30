# Privacy Policy

**Last updated:** April 30, 2026

**TELECOM TOWER POWER LTDA.** ("we") processes personal data in compliance with Brazilian LGPD (Law 13.709/2018) and the Internet Civil Framework.

---

## 1. Controller

- **Legal name:** TELECOM TOWER POWER LTDA.
- **Headquarters:** Brasília – DF, Brazil
- **Data Protection Officer (DPO):** [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br)

## 2. What we collect

| Category | Data | Purpose | LGPD legal basis (art. 7) |
|---|---|---|---|
| Account | Email, name (optional) | API key provisioning, billing | Contract performance (V) |
| Payment | Tokenized by Stripe (no PAN stored) | Charging | Contract performance (V) |
| API usage | IP, User-Agent, endpoint, timestamp, status | Anti-fraud, capacity, security | Legitimate interest (IX) |
| Audit logs | Key issuance, SSO login, admin actions | SOC 2 compliance, incident response | Legal obligation (II) |
| Communications | Support tickets, emails | Customer service | Contract performance (V) |

**We do not collect sensitive data** (race, health, biometrics, political opinion).

## 3. How we use data

- Service provisioning and billing.
- Abuse detection (rate-limit, bot signup, card fraud).
- Operational notifications (incidents, trial expiry).
- Aggregated, non-identifying analytics for roadmap.

**We do not sell your data.** We do not share with third parties for marketing.

## 4. Subprocessors

We share strictly required data with:

| Subprocessor | Purpose | Country | Safeguard |
|---|---|---|---|
| Amazon Web Services (AWS) | Hosting, DB, S3 | Brazil (sa-east-1) + USA (us-east-1) | AWS DPA, encryption at rest/transit |
| Stripe Payments Inc. | Card processing | USA | Stripe DPA, PCI-DSS L1 |
| Anthropic / Amazon Bedrock | AI models (Claude) | USA | Zero-retention processing, prompts not used for training |
| Cloudflare | Anti-bot (Turnstile) and CDN | Global | Cloudflare DPA |
| Sentry / Grafana | Observability | Brazil + EU | Pseudonymized data |
| AWS SES | Transactional email | USA (us-east-1) | AWS DPA |

Full list at the legal index (subprocessors page in preparation).

## 5. International transfer

Some operations occur outside Brazil (mostly USA). Transfers comply with LGPD art. 33: standard contractual clauses with each subprocessor and/or ANPD-recognised adequacy.

## 6. Retention

| Data | Retention |
|---|---|
| API logs | 30 days |
| Audit logs | 7 years (tax/SOC 2) |
| Account data | Until cancellation + 5 years (CDC art. 27) |
| Encrypted backups | 14 days (rolling) |

## 7. Your rights (LGPD art. 18)

You may, free of charge, request:

1. Confirmation of processing;
2. Access to data;
3. Correction of incomplete or outdated data;
4. Anonymization, blocking, or deletion of unnecessary data;
5. Portability;
6. Deletion of consent-based data;
7. Disclosure of sharing;
8. Withdrawal of consent.

Email [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br). Response within **15 days**.

You may also complain directly to the Brazilian DPA (**ANPD**, [www.gov.br/anpd](https://www.gov.br/anpd)).

## 8. Security

- TLS 1.2+ enforced on all connections.
- At-rest encryption (AWS KMS) for DB and S3.
- API keys are hashed (SHA-256) — never stored in cleartext.
- Per-key and per-IP rate limiting; CAPTCHA on free signup.
- Immutable audit logs with IP + UA on key issuance.
- Encrypted backups, quarterly key rotation.
- SOC 2 Type II programme in progress (gap analysis at [/compliance/soc2/](../compliance/soc2/README.md)).

## 9. Cookies

The marketing site and portal use only strictly necessary cookies (session, CSRF). No advertising cookies.

## 10. Children

The service is for businesses and professionals. We do not knowingly collect data from anyone under 18.

## 11. Changes

Material changes will be announced by email 30 days in advance. Previous versions remain in the public docs repository history.

## 12. Contact

- **DPO:** [dpo@telecomtowerpower.com.br](mailto:dpo@telecomtowerpower.com.br)
- **Support:** [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br)
- **Postal address:** to be published (Brasília – DF)
