# 04 — Incident Response Policy

| | |
|---|---|
| **Owner** | SRE / Security |
| **Review cadence** | Annual; tabletop yearly |
| **Approver** | CTO |
| **Last reviewed** | 2026-04 |
| **Mapped controls** | CC2.2, CC2.3, CC4.2, CC7.3, CC7.4, CC7.5 |

## 1. Severity definitions

| Severity | Criteria | Response time | Communication |
|---|---|---|---|
| **P1 (Critical)** | Customer-facing outage; data confidentiality breach; payment system down | ≤ 15 min ack, on-call paged via PagerDuty | Status page within 30 min; customer notification within 4 h (or 72 h if breach per LGPD) |
| **P2 (High)** | Significant feature degradation; partial outage; security vulnerability with active exploit | ≤ 1 h ack | Status page within 2 h |
| **P3 (Medium)** | Non-critical bug; cosmetic issue affecting many users | ≤ 1 business day | Internal only |
| **P4 (Low)** | Minor issue; documentation gap | Best-effort | Internal only |

## 2. Detection sources

- Prometheus alert → Alertmanager → PagerDuty (critical) / Slack (warning).
- Synthetic monitor (GitHub Actions) → Slack on failure → PagerDuty if persistent.
- Customer report via `support@telecomtowerpower.com.br` → triaged by support, escalated to on-call if P1/P2.
- Security report via `security@telecomtowerpower.com.br` → routed directly to Security lead.

## 3. Response process

1. **Detect & ack** — On-call engineer acknowledges the page; opens incident channel `#inc-<YYYYMMDD-slug>` in Slack.
2. **Triage** — Assign severity. Page additional responders if needed. Update status page.
3. **Mitigate** — Stop bleeding first, root-cause later. Document each mitigation step in the incident channel.
4. **Communicate** — Status page updates every 30 min (P1) / 60 min (P2). Customer notification per severity matrix.
5. **Resolve** — Confirm fix, monitor for recurrence, close PagerDuty incident.
6. **Postmortem** — Within 5 business days. Blameless format. Includes timeline, root cause, impact, contributing factors, and corrective actions with owners + due dates. Filed in `evidence/incidents/<YYYY-MM-DD-slug>/postmortem.md`.
7. **Closure** — Corrective actions tracked to closure in GitHub issues; closure date captured in postmortem.

## 4. Roles during an incident

- **Incident Commander (IC):** First on-call to ack. Coordinates response; does *not* execute fixes themselves once others arrive.
- **Operations:** Executes mitigations and fixes.
- **Communications:** Updates status page and customer comms.
- **Scribe:** Maintains timeline in the Slack channel.

For minor incidents, IC may hold multiple roles.

## 5. Customer notification

- **Service incidents:** Status page + email to `notify@<customer-domain>` per Ultra contract; banner in app.
- **Security/privacy breaches:** ≤ 72 h to data subjects per LGPD Art. 48 + GDPR Art. 33; legal review before sending.

## 6. Tabletop exercises

- Annual tabletop covering at least one scenario from: (a) database compromise, (b) DDoS, (c) sub-processor outage (e.g., Stripe down), (d) ransomware on workforce device, (e) insider threat.
- Output: lessons-learned document → backlog of improvements.

## 7. Evidence

- PagerDuty incident exports.
- Postmortems in `evidence/incidents/<YYYY-MM-DD-slug>/`.
- Tabletop after-action reports in `evidence/incidents/tabletop/`.
- Status page history (Statuspage public archive).
