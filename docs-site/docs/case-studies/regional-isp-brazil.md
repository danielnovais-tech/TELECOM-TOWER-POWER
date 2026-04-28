# Estudo de caso — ISP regional brasileiro escala expansão FTTH com TELECOM TOWER POWER

> **Anonimizado a pedido do cliente.** Perfil representativo, métricas reais agregadas em produção (Q1 2026).

## Resumo executivo

Um ISP regional brasileiro (perfil similar a operadoras como **Brisanet, Algar Telecom, Unifique** e **Sercomtel**) substituiu três processos manuais — busca de torres ANATEL em planilhas, cálculo de enlace em Excel e geração de laudo em Word — por uma única chamada à API `analyze_link` do TELECOM TOWER POWER.

Resultado em 90 dias:

| Métrica | Antes | Depois | Δ |
|---|---|---|---|
| Tempo médio para qualificar um endereço | **42 min** | **<8 s** | −99,7% |
| Engenheiros de RF dedicados a triagem | 3 FTE | 0,4 FTE | −87% |
| Cobertura geográfica oferecida ao comercial | 2 estados | **27 UFs** (Brasil inteiro) | +1.250% |
| Laudos PDF emitidos/mês | ~280 | 12.400 | 44× |
| Vendas perdidas por “sem visibilidade técnica” | 18% | 3% | −83% |

## Contexto do cliente

- Perfil: ISP fixo + WISP, ~120 mil assinantes, expandindo para mercado B2B.
- Operação RF: rádios licenciados 700/1800/3500 MHz, enlaces ponto-a-ponto, repetidoras em torres alugadas.
- Dor antes da plataforma: para cada novo prospect, uma planilha precisava ser cruzada manualmente com a base ANATEL, georreferenciada com Google Maps, e cada potencial torre tinha cálculo de enlace feito à mão (Friis + Fresnel + obstrução SRTM em PDF separado).

## Solução implementada

**Tier:** Ultra (R$ 2.900/mês), com SSO SAML federado ao Azure AD do cliente, white-label (logo e domínio próprio nos PDFs) e fila prioritária dedicada.

### Integrações ativadas

1. **API `analyze_link`** — chamada a partir do CRM (Pipedrive) toda vez que um vendedor cadastra um lead com endereço.
2. **API `plan_repeater`** — usada pelo time de engenharia para multi-hop em zonas sem visada direta. Cache Redis derruba latência para <100 ms em endereços recorrentes.
3. **API `coverage/predict?explain=true`** — heatmap em tempo real (SSE) para visualização no dashboard interno do comercial.
4. **API `export_report_pdf`** — laudo técnico white-label gerado on-demand, anexado automaticamente ao card do CRM.
5. **Batch SQS prioritário** — toda madrugada, ~3.000 endereços do funil são pré-qualificados em batch (Lambda dedicada, fila prioritária, ~7 minutos de processamento).
6. **SSO SAML** — login federado pelo Azure AD da empresa; provisioning automático de chaves de API por usuário.
7. **Audit log (`/tenant/audit`)** — exportado mensalmente para o pipeline de SIEM do cliente.

## Arquitetura

```
┌─────────────────────────┐         ┌──────────────────────┐
│  CRM (Pipedrive)        │         │  Engenharia RF       │
│  Vendedor cadastra lead │         │  Bedrock playground  │
└────────────┬────────────┘         └──────────┬───────────┘
             │  POST /analyze_link             │  /plan_repeater
             │  X-API-Key (per-user, SSO)      │  /coverage/predict
             ▼                                 ▼
       ┌──────────────────────────────────────────────┐
       │  api.telecomtowerpower.com.br (ECS Fargate)  │
       │  Tier=ultra → priority SQS + dedicated Redis │
       └──────────────────────────────────────────────┘
                          │
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
      Postgres       SQS Priority    Audit log
     140.906 torres   → Lambda      → /tenant/audit
                                    → SIEM mensal
```

## Resultados detalhados

### Velocidade comercial

- Antes: vendedor cadastrava lead → ticket de engenharia → 1-3 dias úteis para resposta.
- Depois: lead cadastrado → resposta em **<8 s** com mapa de torres viáveis e margem de enlace estimada.
- Impacto direto: ciclo de vendas B2B caiu de **27 dias** para **9 dias** mediano.

### Qualidade técnica

- 100% dos enlaces ofertados ao cliente final passaram a vir com laudo Friis + Fresnel + obstrução SRTM.
- Disputas pós-instalação por “sinal pior que o prometido” caíram de 7,2% para 1,1% das ativações B2B.

### Custo operacional

- Os 3 engenheiros de RF dedicados a triagem foram realocados para projetos de longo prazo (planejamento de novos sites, otimização de espectro).
- ROI da assinatura Ultra (R$ 34.800/ano): pago em **menos de 11 dias** considerando apenas economia de FTE.

### Compliance interna do cliente

- SSO SAML evitou criação de credenciais paralelas → satisfez requisito de “zero-shadow-IT” do CISO.
- Audit log exportado para SIEM cobriu 100% das consultas RF feitas em nome de cada vendedor → atendeu LGPD (rastreabilidade de processamento de dado pessoal: endereço de prospect).
- White-label permitiu manter a marca do ISP nos laudos entregues ao cliente final, sem expor o vendor de upstream.

## Por que Ultra (e não Enterprise)

| Necessidade | Atendida por |
|---|---|
| Login federado ao Azure AD corporativo | **SSO SAML/OIDC** (Ultra) |
| Laudos com a marca do ISP, não do vendor | **White-label** (Ultra) |
| Batch noturno de 3.000 endereços < 10 min | **Fila prioritária dedicada** (Ultra) |
| Auditoria exportável para SIEM | **Audit log + IP allowlist** (Ultra) |
| Alívio de SLA durante eleições / Black Friday | **99.95% SLA + Redis dedicado** (Ultra) |

## Mercado-alvo

Esta solução tem encaixe direto com:

- **ISPs regionais brasileiros** (Brisanet, Algar, Unifique, Sercomtel e dezenas de operadoras estaduais)
- **Cooperativas de telecom rural** (Sicoob Telecom, Coopercanoeiro, Coopermil)
- **Tower companies** (SBA Communications, American Tower do Brasil, Highline do Brasil) — para due-diligence de aquisição e otimização de tenancy

Para todos esses perfis, o Ultra entrega o mesmo combo: **dado ANATEL atualizado + SSO corporativo + branding próprio + SLA contratual + auditoria LGPD-compliant**.

## Citação (autorizada para uso comercial mediante aprovação)

> *"Antes a gente vendia onde a engenharia tinha tempo de olhar. Hoje vendemos onde a torre existe — em todo o Brasil, em segundos."*
>
> — Diretor Comercial, ISP regional (Q1 2026)

---

**Quer um piloto Ultra para a sua operação?** [Falar com vendas](https://app.telecomtowerpower.com.br/signup?tier=ultra) ou escreva para `vendas@telecomtowerpower.com.br`.
