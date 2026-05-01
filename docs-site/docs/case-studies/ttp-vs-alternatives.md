# TTP vs alternativas — escalada de economia anual no Brasil

Comparativo direto entre Telecom Tower Power e as ferramentas que equipes de planejamento RF brasileiras tipicamente usam hoje. Foco: **custo total anualizado** e **economia que TTP entrega em cada faixa de empresa**.

> Última revisão: 2026-04-30. Preços de licenças concorrentes refletem cotações públicas e contratos típicos no mercado BR; podem variar até ±30% por seat / volume.

## Resumo: custo anual por alternativa (1 seat / 1 equipe pequena)

| Plataforma | Categoria | Custo anual BR (R$) | Onde roda | Dado de torre BR incluso |
|---|---|---:|---|:---:|
| **Telecom Tower Power — Pro** | SaaS BR-nativo | **4.188** | AWS sa-east-1 | ✅ 140k torres ANATEL+OCID |
| **TTP — Business** | SaaS BR-nativo | **15.588** | AWS sa-east-1 | ✅ |
| **TTP — Enterprise** | SaaS BR-nativo | **22.680** | AWS sa-east-1 | ✅ |
| **TTP — Ultra** | SaaS BR-nativo | **34.800** | AWS sa-east-1 | ✅ |
| Forsk **Atoll** (1 seat módulo macro) | Desktop, Tier-1 | 80.000 – 150.000 | On-prem Windows | ❌ (cliente importa) |
| Pathloss 5/6 (perpétua amortizada 5a) | Desktop, link PtP | 8.000 – 15.000/ano | On-prem Windows | ❌ |
| iBwave Design (indoor + outdoor add-on) | Desktop, indoor | 60.000 – 90.000 | On-prem Windows | ❌ |
| EDX SignalPro | Desktop, regional | 35.000 – 70.000 | On-prem Windows | ❌ |
| CelPlan CellDesigner | Desktop+serviço | 100.000+ (+SoW) | On-prem | ⚠️ via consultoria |
| Planet (Infovista) | Desktop, Tier-1 | 120.000 – 250.000 | On-prem | ❌ |
| Google Earth + planilha + visita | DIY artesanal | 0 (licença) + R$ 80-300k em horas | Local | ❌ |
| Consultoria RF terceirizada (BR) | Serviço | 200.000 – 600.000 | — | depende |

> Atoll, Planet, iBwave e CelPlan são padrão Tier-1 e fazem o que TTP **não pretende fazer** (ray-tracing 5G mmWave detalhado, downtilt fino, MOCN). TTP é a opção certa quando o caso de uso é **planejamento estratégico, scouting e laudo regulatório em larga escala** — não engenharia fina de RAN.

## Escalada de economia anual por porte de empresa

A tabela a seguir mostra quanto cada perfil de cliente brasileiro gasta hoje **sem TTP** e quanto passa a gastar **com TTP**. A diferença é a economia anual líquida.

| Porte de empresa | Stack atual típico | Custo anual atual | Stack com TTP | Custo anual com TTP | **Economia/ano** |
|---|---|---:|---|---:|---:|
| **WISP regional** (5–30k assinantes) | Google Earth + planilha + 60 visitas/ano | R$ 162.000 | TTP Pro + 20 visitas/ano | R$ 49.000 | **R$ 113.000** |
| **Consultoria RF** (1–3 consultores) | Pathloss + planilha | R$ 223.000 (capacidade limitada) | Pathloss + TTP Business | R$ 339.000 (cap. 3×) | **R$ 105.000 margem extra** |
| **ISP regional grande** (50–200k) | 2 FTE eng. + planilha + 200 visitas | R$ 950.000 | TTP Enterprise + 0,5 FTE + 60 visitas | R$ 290.000 | **R$ 660.000** |
| **Tier-2/3 regional** (Algar, Sercomtel, Brisanet) | Atoll + consultoria externa | R$ 450.000 | Atoll + TTP Ultra (substitui consultoria) | R$ 225.000 | **R$ 225.000** |
| **Tier-1 nacional** (Vivo, TIM, Claro) | Atoll + Planet + CelPlan + equipe interna | R$ 5–15 M | TTP **não substitui** este stack | inalterado | **~R$ 0** |

> Ler como: o **custo anual com TTP** já inclui o ARR do plano. A coluna economia é líquida da assinatura.

### Onde a economia escala de fato

- **WISP → ISP regional grande**: economia cresce de R$ 113k para R$ 660k (**5,8×**) à medida que a operação cresce, porque os custos de scouting e horas de engenheiro escalam linearmente com o número de torres analisadas, mas o preço do TTP sobe pouco (Pro R$ 4,2k → Enterprise R$ 22,7k).
- **Consultoria RF**: economia vira **alavancagem de receita** (3× mais laudos com a mesma equipe), não corte de custo. Pathloss continua licenciado.
- **Tier-2/3 regional**: TTP substitui *consultoria externa pontual*, não Atoll. O ROI de 6× cobre o ticket Ultra.
- **Tier-1 nacional**: TTP não substitui Atoll/Planet/CelPlan. Não é ICP.

## Comparação perfil-a-perfil contra cada concorrente

As tabelas abaixo decompõem o custo anual total (licenças + horas humanas + scouting + retrabalho) para cada combinação realista de stack, em cada perfil. Δ = custo do stack − custo da opção TTP recomendada.

### Perfil 1 — WISP regional (5–30k assinantes)

| Stack | Licença/SaaS | Horas humanas | Scouting | Retrabalho ANATEL | **Total/ano** | Δ vs TTP Pro |
|---|---:|---:|---:|---:|---:|---:|
| **TTP Pro** | R$ 4.188 | R$ 18.000 | R$ 24.000 | R$ 3.000 | **R$ 49.188** | — |
| Google Earth + planilha (DIY) | 0 | R$ 72.000 | R$ 72.000 | R$ 18.000 | R$ 162.000 | **+R$ 113k** |
| Atoll (1 seat) | R$ 100.000 | R$ 50.000 | R$ 36.000 | R$ 6.000 | R$ 192.000 | **+R$ 143k** |
| EDX SignalPro | R$ 50.000 | R$ 45.000 | R$ 36.000 | R$ 6.000 | R$ 137.000 | **+R$ 88k** |
| Consultoria RF terceirizada | 0 | 0 | (incluso) | (incluso) | R$ 240.000 | **+R$ 191k** |

> Para WISP, **TTP é o piso de custo**. Atoll é overkill (ROI negativo neste porte). DIY parece grátis mas custa R$ 113k/ano em horas e visitas evitáveis.

### Perfil 2 — Consultoria RF (1–3 consultores)

Métrica é margem anual gerada (não corte de custo) — TTP triplica capacidade da equipe:

| Stack | Custo ferramentas | Capacidade laudos/ano | Receita | Margem 40% | Δ vs TTP+Pathloss |
|---|---:|---:|---:|---:|---:|
| Pathloss puro | R$ 12.000 | 30 | R$ 150.000 | R$ 60.000 | -R$ 105k |
| **Pathloss + TTP Business** | **R$ 27.588** | **90** | **R$ 450.000** | **R$ 165.000** | — |
| Pathloss + Atoll seat | R$ 112.000 | 50 | R$ 250.000 | R$ 100.000 | -R$ 65k |
| iBwave Design + Pathloss | R$ 87.000 | 40 | R$ 200.000 | R$ 80.000 | -R$ 85k |

> A alavancagem (3× capacidade) supera o ganho marginal de precisão de Atoll/iBwave para o caso típico de laudo regulatório / viabilidade.

### Perfil 3 — ISP regional grande (50–200k assinantes)

| Stack | Licenças/SaaS | Engenharia (FTE) | Scouting | Atrasos homologação | **Total/ano** | Δ vs TTP Enterprise |
|---|---:|---:|---:|---:|---:|---:|
| **TTP Enterprise** | R$ 22.680 | R$ 125.000 (0,5 FTE) | R$ 90.000 | R$ 50.000 | **R$ 287.680** | — |
| Planilha + 2 FTE | 0 | R$ 500.000 | R$ 300.000 | R$ 150.000 | R$ 950.000 | **+R$ 662k** |
| Atoll (2 seats) + 1,5 FTE | R$ 220.000 | R$ 375.000 | R$ 150.000 | R$ 75.000 | R$ 820.000 | **+R$ 532k** |
| EDX (2 seats) + 1,5 FTE | R$ 110.000 | R$ 375.000 | R$ 180.000 | R$ 100.000 | R$ 765.000 | **+R$ 477k** |
| Planet (1 seat) + 2 FTE | R$ 180.000 | R$ 500.000 | R$ 200.000 | R$ 100.000 | R$ 980.000 | **+R$ 692k** |

> Maior salto de economia. TTP Enterprise entrega 4× mais ROI que Atoll neste porte porque o gargalo é **velocidade de iteração**, não precisão de modelo.

### Perfil 4 — Tier-2/3 regional (Algar, Sercomtel, Brisanet)

Atoll já é sunk cost — comparação é sobre o que TTP elimina (consultoria externa + retrabalho rural):

| Stack | Atoll (sunk) | Consultoria externa | Retrabalho rural | TTP / outros | **Total/ano** | Δ vs Atoll+TTP Ultra |
|---|---:|---:|---:|---:|---:|---:|
| **Atoll + TTP Ultra** | (sunk) | R$ 150.000 | R$ 25.000 | R$ 34.800 | **R$ 209.800** | — |
| Atoll puro (status quo) | (sunk) | R$ 350.000 | R$ 100.000 | 0 | R$ 450.000 | **+R$ 240k** |
| Atoll + iBwave outdoor | (sunk) | R$ 250.000 | R$ 75.000 | R$ 80.000 | R$ 405.000 | **+R$ 195k** |
| Atoll + CelPlan SoW | (sunk) | R$ 150.000 | R$ 50.000 | R$ 100.000 | R$ 300.000 | **+R$ 90k** |

> TTP Ultra **substitui consultoria externa pontual**, não Atoll.

### Perfil 5 — Tier-1 nacional (Vivo, TIM, Claro)

| Stack | Custo anual | Δ |
|---|---:|---:|
| Atoll + Planet + CelPlan + 8–15 FTE (status quo) | R$ 5–15 M | — |
| + TTP Custom (piloto experimental) | + R$ 60–200k | **economia ≈ R$ 0** |

> TTP **não substitui nada** neste stack. Confirmado fora do ICP — ver `notes/tier1-roadmap.md`.

## Resumo: Δ de economia por concorrente

### Onde TTP **substitui** o concorrente

| Concorrente substituído | Quem migra | Economia anual mediana |
|---|---|---:|
| Consultoria RF terceirizada (volume) | WISP, ISP regional | **R$ 150–250k** |
| Google Earth + planilha (DIY) | WISP pequeno-médio | **R$ 110k** |
| Planet (Infovista) "tático" | ISP regional grande migrando de Tier-1 herdado | **R$ 690k** |
| Atoll seat ocioso (não-Tier-1) | ISP regional, consultoria pequena | **R$ 140k** |
| EDX SignalPro | ISP regional, integradores | **R$ 90k** |

### Onde TTP **complementa** (sem substituir)

| Concorrente complementado | Quem combina | Economia incremental |
|---|---|---:|
| Pathloss (link PtP fino) | Consultoria RF | **R$ 105k** (margem extra via 3× capacidade) |
| Atoll on-prem | Tier-2/3 regional | **R$ 220k** (corta consultoria externa) |
| iBwave Design (indoor) | Integrador de prédios | **R$ 50–80k** (outdoor passa para TTP) |

### Regra de bolso

- **WISP ou ISP regional?** TTP entrega R$ 100–700k/ano de economia. Qualquer concorrente custa mais e entrega menos para esse caso.
- **Consultoria RF?** TTP + Pathloss vence Pathloss + Atoll em margem (3× capacidade > +20% precisão).
- **Tier-2/3 regional com Atoll?** TTP Ultra paga ~6× ao cortar consultoria externa.
- **Tier-1?** TTP não economiza nada. Mantenha o stack atual.

## TTP vs Atoll (Forsk) — comparação direta

| Dimensão | Atoll | TTP |
|---|---|---|
| Alvo | Operadora Tier-1, ray-tracing 3D, MOCN, MIMO 5G | Scouting, viabilidade, laudo regulatório |
| Onde roda | Desktop Windows on-prem | SaaS, navegador |
| Setup inicial | 4–12 semanas (clutter, antenas, ajuste) | 5 minutos |
| Treinamento | 2–4 semanas, certificação | 1 hora de onboarding |
| Custo 1 seat/ano | R$ 80–150k + servidor | R$ 4–35k |
| Dado BR incluso | ❌ cliente importa MOSAICO/IBGE | ✅ 140k torres curadas, SRTM 30m |
| Compartilhar resultado | export PDF/KMZ manual | URL pública por job |
| API REST | módulo extra | nativa, OpenAPI |
| Auditoria de previsão | manual | hash do modelo + commit hash em cada PDF |

**Recomendação honesta**: se você é Tier-1 BR, mantenha Atoll. Se é WISP/ISP regional/consultoria, TTP cobre 80% do trabalho diário a 5–15% do custo.

## TTP vs Pathloss (link PtP)

Pathloss continua sendo a referência para link ponto-a-ponto licenciado SCPC/SDH, especialmente para anomalia de propagação K-factor e diversidade de espaço. **TTP não substitui Pathloss para esse caso**.

Onde TTP complementa:

- Identificar candidatos de torre antes do trabalho fino no Pathloss (TTP filtra 50 torres → Pathloss aprofunda 5)
- Gerar relatório de viabilidade de cliente final com cobertura macro
- Auditar o portfólio inteiro de links em segundos para identificar onde refazer estudo

## TTP vs DIY (Google Earth + planilha)

| Métrica | Google Earth + planilha | TTP |
|---|---|---|
| Tempo para responder "qual torre dá cobertura no CEP X?" | 2–4 horas | 30 segundos |
| Erro típico em distância / azimute | manual, sujeito a erro | calculado |
| SRTM / Fresnel | manual, ~1h por enlace | automático |
| Clutter rural BR | ❌ | parcial (modelo ML calibrado, RMSE 12,94 dB) |
| Reprodutibilidade | baixa | hash de modelo + commit hash |
| Custo R$ direto | 0 | R$ 4,2k–34,8k/ano |
| Custo em horas humanas | **R$ 80–300k/ano** | residual |

A migração mais frequente é **DIY → TTP Pro** (R$ 4,2k/ano substitui ~R$ 100k em horas).

## TTP vs consultoria RF terceirizada

Consultoria RF de qualidade no BR cobra R$ 250–400/h ou pacote por laudo (R$ 3–8k). Para uma operação que precisa de 30+ laudos/ano, terceirizar custa R$ 90–240k/ano.

TTP **não substitui** consultoria de campo (drive test, comissionamento, otimização KPI). Substitui:

- Pré-laudo regulatório ANATEL
- Análise de viabilidade comercial em escala
- Estudo de cobertura para licitação / RFP
- Due diligence em M&A de pequenas operadoras

Operações que rodam TTP internamente **e** mantêm consultoria pontual para validação reduzem o gasto de consultoria em 60–80%.

## Como ler esta comparação

- Os números acima são **bottom-up**, baseados em premissas de [roi-by-segment.md](roi-by-segment.md) e cotações públicas.
- A categoria importa mais que o preço: nenhuma ferramenta na lista resolve todos os casos. **TTP, Atoll, Pathloss e iBwave são complementares**, não substitutos diretos.
- A pergunta correta para o comprador BR não é "qual é mais barato?" e sim **"para o meu caso de uso, qual combinação mínima de ferramentas resolve 95% do trabalho?"**

Para WISPs e ISPs regionais, a resposta cada vez mais é: **TTP sozinho** (com escalada para Pathloss/Atoll quando o problema vira engenharia fina de RAN).

## Limitações desta análise

- Preços de concorrentes são públicos ou estimados via cotações 2025–2026; não auditados
- Não inclui custo de migração, treinamento ou integração com OSS/BSS do cliente
- Não considera ganhos qualitativos (velocidade comercial, qualidade de proposta, redução de risco regulatório)
- Não considera o caso onde o cliente já tem licenças sunk-cost (Atoll/Pathloss já comprado é tratado como custo zero marginal)
