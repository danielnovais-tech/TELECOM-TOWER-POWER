# ROI por segmento

Estimativa de economia anual e ROI para os 5 perfis-alvo da plataforma. Números são *bottom-up* baseados em premissas de custo-hora e taxas de mercado brasileiras em 2026, não em medições de clientes pagos. Após 5+ clientes fechados, esta página será revisada com dados reais.

> Última revisão: 2026-04-30. Para premissas detalhadas, ver final da página.

## Resumo

| Segmento | Plano | ARR cliente | Economia líquida | ROI |
|---|---|---:|---:|:---:|
| WISP regional (5-30k assinantes) | Pro (R$ 349/mês) | R$ 4.188 | **~R$ 115.000** | 27× |
| Consultoria RF (1-3 consultores) | Business (R$ 1.299/mês) | R$ 15.588 | **~R$ 110.000** | 7× |
| ISP regional grande (50-200k) | Enterprise (R$ 1.890/mês) | R$ 22.680 | **~R$ 660.000** | 29× |
| Tier-2/3 regional (Algar, Sercomtel, Brisanet) | Ultra (R$ 2.900/mês) | R$ 34.800 | **~R$ 220.000** | 6× |

> **Cliente médio recupera o investimento anual em 14 dias.**

## WISP regional — Pro

**Perfil:** 5.000-30.000 assinantes, 1-2 engenheiros, planejamento em Google Earth + planilha, scouting físico em 80% das prospecções.

| Item | Sem TTP | Com TTP | Economia |
|---|---:|---:|---:|
| Visitas de scouting (~60/ano × R$ 1.200) | R$ 72.000 | R$ 24.000 (descarta 67% pela API) | R$ 48.000 |
| Tempo de eng. em planilhas (8 h/sem × R$ 180) | R$ 72.000 | R$ 18.000 (2 h/sem) | R$ 54.000 |
| Pareceres rejeitados na ANATEL (3-5/ano × R$ 6k) | R$ 18.000 | ~R$ 3.000 | R$ 15.000 |
| Custo TTP Pro | — | R$ 4.188 | (R$ 4.188) |
| **Líquido** | | | **~R$ 113.000** |

Payback: **14 dias**. ROI: **27×**.

## Consultoria RF independente — Business

**Perfil:** 1-3 consultores, Pathloss licenciado, 20-50 laudos/ano a R$ 3-8k cada. Ganho com TTP é principalmente *alavancagem de receita*, não redução de custo.

| Item | Sem TTP | Com TTP | Delta |
|---|---:|---:|---:|
| Tempo médio por laudo | 6-12 h | 2-4 h | -67% |
| Capacidade de laudos/ano (mesma equipe) | ~30 | ~90 | +200% |
| Receita anual (R$ 5k médio/laudo) | R$ 150.000 | R$ 450.000 | R$ 300.000 |
| Margem ~40% | R$ 60.000 | R$ 180.000 | **R$ 120.000** |
| Custo TTP Business | — | R$ 15.588 | (R$ 15.588) |
| **Líquido em margem** | | | **~R$ 105.000** |

Pathloss segue licenciado — TTP complementa para os ~70% de laudos que não exigem o nível de detalhe do Pathloss. ROI: **7×**.

## ISP regional grande — Enterprise

**Perfil:** 50.000-200.000 assinantes, 3-8 engenheiros RF, ferramenta interna improvisada (sem Atoll). Tipicamente operadora regional de FTTH expandindo FWA.

| Item | Sem TTP | Com TTP | Economia |
|---|---:|---:|---:|
| Scouting (~200 visitas/ano × R$ 1.500) | R$ 300.000 | R$ 90.000 | R$ 210.000 |
| 2 FTE engenheiros em planejamento manual (R$ 250k cada) | R$ 500.000 | R$ 125.000 (0,5 FTE) | R$ 375.000 |
| Atrasos em homologação ANATEL (2-4/ano × R$ 50k) | R$ 150.000 | ~R$ 50.000 | R$ 100.000 |
| Custo TTP Enterprise + add-ons | — | R$ 25.000 | (R$ 25.000) |
| **Líquido** | | | **~R$ 660.000** |

ROI: **29×**. Aqui o ticket Enterprise se justifica plenamente.

## Tier-2/3 regional (Algar, Sercomtel, Brisanet) — Ultra

**Perfil:** Atoll já licenciado, equipe RF dedicada (10-20 pessoas), mas sub-utilizada para projetos pequenos/táticos (M&A, FWA novo bairro, due diligence, regulatório).

TTP **não substitui** Atoll — complementa para nichos onde Atoll é overkill ou clutter rural BR é falho.

| Item | Sem TTP | Com TTP | Economia |
|---|---:|---:|---:|
| Equipe Atoll para projetos táticos (200 h/ano × R$ 250) | R$ 50.000 | R$ 12.500 (50 h/ano) | R$ 37.500 |
| Consultoria externa para leilões 5G FWA / regulatório | R$ 200-400k/ano | R$ 100-200k/ano | R$ 150.000 |
| Cobertura rural onde Atoll não tem clutter calibrado | retrabalho ~R$ 50-100k | TTP cobre | R$ 75.000 |
| Custo TTP Ultra + integrações | — | R$ 35.000 | (R$ 35.000) |
| **Líquido** | | | **~R$ 225.000** |

ROI: **6×**. Aceitável; o ticket Ultra (~R$ 35k/ano) cobre o esforço de venda enterprise.

## Tier-1 nacional (Vivo, TIM, Claro)

Não recomendado — análise interna em `notes/tier1-roadmap.md` (repositório). Resumo: ROI de **1-2× para o cliente** vs custo de venda de **R$ 1-2 M para o vendedor** (procurement, SOC 2, ISO 27001, pen test, AE sênior). Inviável até receita base passar de R$ 5 M ARR.

## Premissas

| Recurso | Valor |
|---|---|
| Hora de eng. RF sênior CLT carregado | R$ 180 |
| Hora de consultor RF PJ | R$ 250-400 |
| Licença Atoll (1 seat anual) | R$ 80-150k |
| Licença Pathloss perpétua amortizada | R$ 8-15k/ano equivalente |
| Visita técnica scouting (ida-volta + diária) | R$ 800-2.500 |
| GIS analyst | R$ 120/h |
| CSV ANATEL curado de terceiro | R$ 500-2.000/mês |
| Margem média de consultoria RF | 40% |

Os números acima refletem mercado brasileiro 2026 e devem ser validados com cada cliente antes de uso comercial. Não usar como compromisso contratual.

## Limitações da estimativa

- Baseada em entrevistas qualitativas e benchmarking público, não em dados de clientes pagos
- Não inclui custos de integração / treinamento (tipicamente 10-30 h por cliente)
- Não inclui custo de oportunidade de migração (~30 dias até produtividade plena)
- Sub-estima ganho não-financeiro: velocidade de proposta comercial, qualidade de laudo, redução de risco regulatório
