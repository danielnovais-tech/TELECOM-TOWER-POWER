# Tier-1 Roadmap (TIM → Vivo)

> **Nota interna.** Não publicado pelo MkDocs (esta pasta não está em `mkdocs.yml`). Versionado para consulta. Última revisão: 2026-04-30.

## Por que TIM antes de Vivo

TIM é mais permeável que Vivo por 3 razões estruturais:

- **Menor escala de RF interna** — desinvestiu em equipe RF própria após a aquisição da Oi Móvel; usa mais consultoria externa.
- **Cultura de SaaS** — já consome Salesforce, ServiceNow, AWS direto (não só via Embratel).
- **Pressão de capex** — margem mais apertada que Vivo, abre porta para "alugar capacidade analítica" em vez de "comprar Atoll".

Vivo só vem **depois** que houver TIM como referência. Sem isso, não passa do filtro de procurement.

## Roadmap em 4 fases (12-18 meses)

### Fase 1 — Habilitar piloto técnico (próximos 3 meses)

**Objetivo:** poder dizer "sim" quando TIM pedir POC sem reescrever nada.

| Item | Por quê | Esforço |
|---|---|---|
| **BYOD / tenant data import** (`POST /tenant/towers/import` + retrain per-tenant) | Sem isso, qualquer Tier-1 fala "seus dados são públicos, fora" no minuto 1 | 2-3 sprints |
| **VPC peering / PrivateLink endpoint** | "API só acessível pela VPC do cliente, sem internet egress" — não é on-prem, mas atende 80% dos receios | 1 sprint |
| **DPA template assinável** + sub-processor list versionado | Procurement pede no kickoff. Sem isso, processo trava | 1 semana com advogado |
| **SBOM público** (`syft` no CI, publicado em `/security`) | Vira commodity em 2026, ausência é red flag | 2 dias |
| **Pen test externo anual** (Tempest, Conviso ou Hackmetrix BR) | Relatório executivo é entregável de venda, não documento técnico | R$ 25-50k, 4 semanas |

### Fase 2 — Diferencial técnico defensável (3-6 meses)

**Objetivo:** ter resposta para "o que vocês fazem que Atoll não faz?".

| Item | Por quê |
|---|---|
| **Longley-Rice + ITU-R P.1812 fora de beta + per-clutter (MapBiomas)** | Modelo de propagação chega em RMSE 6-8 dB, equivalente a Atoll calibrado em rural |
| **5G FWA coverage prediction** específico para 3.5 GHz e 26 GHz | TIM e Vivo estão expandindo FWA agressivamente; Atoll não tem modelo BR-calibrado para isso |
| **Integração ANATEL Mosaico via API direta** (não só dump mensal) | Vira "fonte autoritativa em tempo real" — Vivo/TIM consultam ANATEL pesado para leilões 5G |
| **Simulador de obrigações de cobertura ANATEL** (BR-050, faixa 700, 5G) com geração de relatório regulatório | Vivo/TIM gastam consultoria cara nisso 2× por ano. Vira "use case que Atoll não faz" |

### Fase 3 — Compliance enterprise (6-12 meses, paralelo à Fase 2)

**Objetivo:** passar no procurement.

| Item | Custo aproximado |
|---|---|
| SOC 2 Type I → Type II | R$ 80-150k consultoria + R$ 60-100k auditoria |
| ISO 27001 | R$ 100-200k |
| LGPD: relatório de impacto (RIPD) público + DPO certificado | R$ 30k |
| Plano de continuidade testado anualmente (BCP/DR exercise documentado) | Interno, ~1 mês |
| Cyber insurance R$ 5-10M | R$ 30-50k/ano |

### Fase 4 — Go-to-market enterprise (paralelo a tudo)

**Objetivo:** chegar à mesa de decisão.

| Item | Por quê |
|---|---|
| **1 case study TIM-friendly antes de bater na TIM** | Algar ou Sercomtel são alvos perfeitos: regional, 5G FWA, dados próprios. Fechá-las e publicar resultado mensurável (ex: "redução 40% scouting") |
| **Account executive sênior com rolodex Brasília/SP** | TIM compra de quem conhece. Não é commodity. Custo R$ 35-50k/mês + comissão |
| **Programa de parceria com integradoras BR** (Embratel/Algar Telecom Service/Sigma/Solidum) | Tier-1 BR compra via parceiro local em ~70% dos casos |
| **Whitepaper com calibração contra dados públicos da Vivo/TIM** | Use ANATEL coverage maps + relatórios trimestrais ANATEL para mostrar que seu modelo casa com KPIs deles |

## Sequência recomendada

```
Mês 0-3   Fase 1 (BYOD + PrivateLink + DPA + pen test)  ← libera POC
Mês 3-6   Fase 2 parte A (Longley-Rice + 5G FWA)         ← diferencial técnico
Mês 4-9   Fase 3 parte A (SOC 2 Type I)                  ← passa procurement
Mês 6-12  Fase 4 parte A (case Algar/Sercomtel + AE)     ← case study
Mês 9-15  TIM piloto                                     ← contrato R$ 200-500k/ano
Mês 12-18 Fase 3 parte B (ISO 27001 + SOC 2 Type II)
Mês 15-24 Vivo após TIM como referência
```

## Custo total estimado

| Categoria | 18 meses |
|---|---|
| Engenharia (Fase 1+2) | ~R$ 600k (1 sênior + 0,5 pleno dedicados) |
| Compliance (Fase 3) | ~R$ 300-450k |
| Pen test + cyber seguro | ~R$ 80k |
| AE + parcerias (Fase 4) | ~R$ 800k |
| **Total** | **~R$ 1,8-2,2M** |

## Gate de decisão

**Fazer Fase 1 sempre** — baixo custo (~R$ 100-150k) e abre TIM piloto **e** vende melhor para Algar/Sercomtel/consultorias grandes hoje. Sem perda.

**Fase 2 e 3 só comprometer depois de:**

- 50+ clientes pagantes ativos no ICP atual
- 1 lead Tier-1 inbound qualificado (RFI assinado)
- Caixa para 18 meses de runway sem precisar de receita Tier-1

Antes disso é **premature optimization** — risco de construir SOC 2 que ninguém pediu, em vez de fechar 100 WISPs que pagam R$ 349/mês.

## Análise pró/contra Vivo (resumo da discussão de 2026-04-30)

Dos 20 prós listados, **apenas 3 sobrevivem ao filtro Tier-1**:

1. **Scouting rural rápido** — útil para equipes de expansão pontual
2. **Expansão pontual / M&A / leilões 5G FWA** — task force temporária
3. **Equipes pequenas internas** (regulatório, LAI, due diligence) — sub-departamentos isolados

ARR potencial em sub-departamentos Vivo: R$ 5-15k/mês. Não é a operação principal.

Os 20 contras são quase todos verdadeiros e não-removíveis sem reconstruir o produto. Aceitar como veredicto correto e mirar TIM/Algar/Sercomtel/Brisanet primeiro.

## Trigger para revisar este documento

- Lead inbound Tier-1 qualificado entrar no funil
- Receita ARR cruzar R$ 1M
- Algar ou Sercomtel virar case study fechado
- Mudança regulatória ANATEL que mude jogo (ex: obrigatoriedade de SaaS auditável para leilões)

## Anexo: ROI por segmento (estimativa, 2026-04-30)

Premissas:

- Eng. RF sênior CLT carregado: R$ 180/h
- Consultor RF PJ: R$ 250-400/h
- Visita técnica de scouting: R$ 800-2.500
- Licença Atoll seat: R$ 80-150k/ano
- Licença Pathloss amortizada: R$ 8-15k/ano

| Segmento | Plano TTP | ARR cliente | Economia anual líquida | ROI cliente | Quantos no BR |
|---|---|---:|---:|:---:|---:|
| WISP regional (5-30k assinantes) | Pro | R$ 4,2k | ~R$ 115k | **27×** | ~3.000 |
| Consultoria RF (1-3 consultores) | Business | R$ 15,6k | ~R$ 110k margem extra | **7×** | ~500-1.000 |
| ISP regional grande (50-200k) | Enterprise | R$ 22,7k | ~R$ 660k | **29×** | ~50-100 |
| Algar / Sercomtel / Brisanet | Ultra | R$ 34,8k | ~R$ 220k | **6×** | ~5-10 |
| TIM (sub-departamento isolado) | Custom | R$ 60-200k | ~R$ 100-280k | 1-2× | 1 |
| Vivo / Claro | Custom | R$ 60-200k | ~R$ 100-280k | **1-2×** | 2 |

**Conclusão operacional:**

- WISPs e ISPs regionais grandes têm ROI 26-29× — payback em ~14 dias. Marketing pode usar como mensagem central.
- Tier-1 BR tem ROI 1-2× para o cliente **e** ROI negativo para o vendedor (custo de venda R$ 1-2M para fechar R$ 60-200k/ano). Não buscar.
- Tier-2/3 regional (Algar, Sercomtel, Brisanet) tem ROI 6× — aceitável, e o ticket Ultra cobre o esforço de venda. **Buscar após Fase 1**.

Detalhamento por linha de economia em [docs-site/docs/case-studies/roi-by-segment.md](../docs-site/docs/case-studies/roi-by-segment.md).
