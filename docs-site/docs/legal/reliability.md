# Confiabilidade

Compromissos operacionais factuais da TELECOM TOWER POWER. Métricas baseadas no que está implementado e medido em produção, não em aspiração.

> Última revisão: 2026-04-30. Status em tempo real: [monitoring.telecomtowerpower.com.br](https://monitoring.telecomtowerpower.com.br/).

## SLA por tier

| Tier | Disponibilidade mensal | Crédito por violação |
|---|---|---|
| Free | Best-effort | — |
| Starter | 99.5% | — |
| Pro | 99.5% | 10% do mês |
| Business | 99.9% | 25% do mês |
| Enterprise | 99.9% | 50% do mês |
| Ultra | 99.95% | 100% do mês |

A janela de cálculo é o mês civil em UTC. Manutenção programada anunciada com ≥72 h de antecedência não conta como indisponibilidade.

## RTO e RPO

| Cenário | RTO | RPO |
|---|---|---|
| Falha de instância ECS Fargate | < 2 min (auto-scaling) | 0 (stateless) |
| Falha de zona de disponibilidade AWS sa-east-1 | < 15 min (multi-AZ RDS + ECS) | < 5 min |
| Falha de região AWS sa-east-1 | < 4 h (failover Railway us-west) | < 24 h |
| Corrupção lógica de banco | < 4 h (point-in-time restore) | < 5 min (RDS PITR) |
| Perda total de conta AWS | < 24 h | < 24 h (S3 cross-region replication — roadmap) |

Drill semanal automatizado verifica restore de Postgres + assertions de integridade.

## Failover quente

- Primário: ECS Fargate em sa-east-1 (api.*)
- Failover: Railway us-west (web, frontend, worker)
- Banco de failover: réplica Postgres assíncrona com lag ≤ 60 s
- Trigger: manual via runbook + automatizado via PagerDuty escalation policy

Detalhes em [Operações/Runbook](../operations/runbook.md).

## Observabilidade

- **Métricas**: Prometheus + Grafana, 14 dias de retenção
- **Logs**: Loki, 30 dias
- **Traces**: Tempo (OpenTelemetry), 7 dias
- **Alertas**: 12 regras Prometheus → Alertmanager → Slack + PagerDuty (critical-only, `send_resolved=true`)
- **Synthetic monitoring**: GitHub Actions cron probes nos 3 entrypoints (api, app, monitoring) a cada 5 min
- **Dashboard público**: [monitoring.telecomtowerpower.com.br](https://monitoring.telecomtowerpower.com.br/)

## Continuidade do negócio (Bus factor)

A empresa é pequena. Tratamos a continuidade como controle de primeira ordem:

| Mitigação | Aplicável a |
|---|---|
| **Source code escrow** (cláusula contratual) | Enterprise, Ultra |
| Runbook público versionado em git | Todos |
| Backups automatizados + restore drill semanal | Todos |
| Documentação de arquitetura completa | Todos |
| CI/CD reproduzível (16 workflows GitHub Actions) | Todos |
| Infrastructure-as-code (ECS task defs, Caddy, Compose) commitados | Todos |
| Knowledge base de incidentes em git | Todos |

O *escrow* permite que clientes Enterprise/Ultra recebam o código-fonte completo (modulo dependências third-party com licenças incompatíveis) caso a empresa cesse operações por mais de 60 dias.

## Histórico de incidentes

Página pública de status: [Status](../operations/status.md).

Postmortems publicados em até 7 dias úteis após incidentes Sev-1 ou Sev-2.

## Manutenção programada

- Janela preferencial: domingos 04:00–06:00 UTC
- Anúncio: email para todos os contatos de billing + banner em `app.*` ≥72 h antes
- Tipicamente zero downtime (rolling deploy ECS); janelas anunciadas só se rolling não for possível

## Endurecimento recente (2026-Q2)

- **Fila prioritária Enterprise/Ultra**: SQS dedicado + Lambda consumer, SLA 99.95% no Ultra
- **Modelo ML em produção**: ridge-v1 (RMSE 12.94 dB, n=20 000) para `/coverage/predict`, com retrain noturno
- **Snap ANATEL por prestadora**: melhora precisão de localização para todas as 12 SMP/SME indexadas
- **Restore drill semanal**: assertions de integridade em `towers`, `api_keys`, `alembic_version` toda segunda 07:15 UTC

## Limitações conhecidas

- **Região única**: sa-east-1 primária. O failover Railway us-west cobre falhas regionais AWS, não falhas globais coordenadas. Multi-region ativo está fora do roadmap atual por custo.
- **Sem auditoria SOC 2 / ISO 27001 externa hoje**. Práticas documentadas e auditáveis em [Compliance/SOC 2](../compliance/soc2/README.md).
- **Equipe pequena**: on-call 24/7 só para Business+; planos inferiores recebem suporte em horário comercial UTC-3.
