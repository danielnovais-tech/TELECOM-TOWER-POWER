# Gap Analysis — SOC 2 Type II Readiness

> Residual gaps as of April 2026. Closed gaps are removed from this file and reflected in [`control-matrix.md`](control-matrix.md).

## Status legend

- 🟢 **Closed** — control fully operating with evidence; just-in-time documentation
- 🟡 **Partial** — control exists but evidence is point-in-time; needs continuous capture
- 🔴 **Open** — gap actively being remediated

## Open & partial items

| # | Control | Gap | Owner | Target close | Status |
|---|---|---|---|---|---|
| 1 | CC1.4.1 | Annual security training tracker exists but completion certs not centrally archived | HR | 2026-Q3 | 🟡 |
| 2 | CC3.2.1 | Threat-model section in PR template is optional; not enforced | Security | 2026-Q3 | 🟡 |
| 3 | CC6.4.1 | Quarterly access reviews documented manually; need scripted snapshot | Security | 2026-Q3 | 🔴 |
| 4 | CC9.2.1 | Vendor SOC reports collected ad-hoc; need annual review schedule with reminders | Security | 2026-Q3 | 🟡 |
| 5 | P5.1.1 | DSAR endpoint still email-based; want self-serve portal in `app.*` | Engineering | 2026-Q4 | 🟡 |
| 6 | P6.1.1 | Sub-processor list page is in mkdocs but not yet legally reviewed | Legal | 2026-Q3 | 🔴 |
| 7 | CC1.2.1 | Advisory-board quarterly review needs first formal session minutes | CTO | 2026-Q3 | 🔴 |

## Recently closed

| # | Control | Resolution | Closed |
|---|---|---|---|
| ✅ | A1.2.2 | Weekly verified restore drill workflow shipped (`backup-restore-drill.yml`) — first run confirmed 140,498 towers + 23 api_keys + 1 alembic_version | 2026-04-27 |
| ✅ | CC4.1.2 | Synthetic monitor probes all three entrypoints | 2026 (in production) |
| ✅ | CC7.3.1 | PagerDuty (critical) + Slack (warning) live with `send_resolved=true` | 2026-04 |
| ✅ | CC6.1.1 | Cognito SSO + MFA enforced for admin Cognito group | 2026-04 |
| ✅ | CC8.1.2 | 16 hardened GitHub Actions workflows with concurrency + retries | 2026-04-27 |

## Pre-audit readiness checklist

Run through this before kicking off the auditor engagement:

- [ ] All 🔴 items above resolved or formally accepted as known limitations
- [ ] All 🟡 items have automation or documented procedure in place
- [ ] `evidence/` S3 prefix populated with at least 90 days of samples
- [ ] `soc2-auditor` IAM role tested (can read CloudTrail, AWS Config, S3 evidence prefix; cannot mutate)
- [ ] Pre-saved CloudWatch Logs Insights queries tested
- [ ] Tabletop exercise completed within last 12 months
- [ ] Annual policy review committed to git within last 12 months
- [ ] Last vendor reviews dated within last 12 months
- [ ] Privacy notice + sub-processor list legally reviewed within last 12 months

## Estimated audit timeline

Once all ⛔ items above close:

| Phase | Duration | Deliverable |
|---|---|---|
| Type I (snapshot) | ~6 weeks | Type I attestation report |
| Type II (continuous) | 90+ days observation + 4 weeks fieldwork | Type II attestation report |

> **Recommendation:** Start with Type I in 2026-Q4 to validate control design, then immediately begin the 90-day Type II observation period — first Type II report ready Q2 2027.
