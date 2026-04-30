# Status

A página de status pública oficial é:

**[https://monitoring.telecomtowerpower.com.br/](https://monitoring.telecomtowerpower.com.br/)** *(Grafana — leitura anônima)*

## O que está exposto

- **Uptime do API** (`up{job="api"}` — Prometheus, último 24 h / 7 d / 30 d)
- **p50 e p95 de latência** por endpoint (`/plan_link`, `/coverage`, `/plan_repeater`, `/health`)
- **Taxa de erro 5xx** (rolling 5 min)
- **Saúde do worker de batch** e profundidade da fila SQS
- **Saúde de Redis e Postgres** via exporters
- **Eventos Stripe** processados nos últimos 7 dias

## Sinópticos automáticos

Um monitor sintético externo executa a cada **30 minutos** sobre `https://api.telecomtowerpower.com.br/health`. O resultado alimenta o painel "Synthetic uptime" do Grafana e dispara alerta no Slack/e-mail em duas falhas consecutivas.

## Janelas de manutenção

Comunicadas com **24 horas de antecedência** via:

- E-mail aos clientes ativos
- Banner no portal
- Anúncio no [status](#)

## Histórico de incidentes

Incidentes pós-mortem ficam em [`operations/runbook.md`](runbook.md) e na seção de release notes do repositório.

## Inscrever-se em alertas

Clientes Business e Enterprise recebem notificação automática por e-mail/webhook em incidentes severidade `critical`. Solicite o webhook em [support@telecomtowerpower.com.br](mailto:support@telecomtowerpower.com.br).
