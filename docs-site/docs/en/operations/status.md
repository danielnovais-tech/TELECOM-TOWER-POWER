# Status

The official public status page is:

**[https://monitoring.telecomtowerpower.com.br/](https://monitoring.telecomtowerpower.com.br/)** *(Grafana — anonymous read-only)*

## What is exposed

- **API uptime** (`up{job="api"}` — Prometheus, last 24 h / 7 d / 30 d)
- **p50 and p95 latency** per endpoint (`/plan_link`, `/coverage`, `/plan_repeater`, `/health`)
- **5xx error rate** (rolling 5 min)
- **Batch worker health** and SQS queue depth
- **Redis and Postgres health** via exporters
- **Stripe events** processed in last 7 days

## Synthetic checks

An external synthetic monitor runs every **30 minutes** against `https://api.telecomtowerpower.com.br/health`. Results feed the "Synthetic uptime" Grafana panel and trigger Slack/email alerts on two consecutive failures.

## Maintenance windows

Announced **24 h in advance** via:

- Email to active customers
- Banner in the portal
- Status page announcement

## Incident history

Post-mortems live in [`operations/runbook.md`](runbook.md) and the repository release notes.

## Subscribe to alerts

Business and Enterprise customers receive automatic email/webhook notification on `critical`-severity incidents. Request a webhook at [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br).
