# Segurança

Esta página descreve as práticas de segurança em produção da TELECOM TOWER POWER. É um documento factual, não promocional: descreve o que está implementado hoje, e o que está no roadmap.

> Última revisão: 2026-04-30. Para reportar uma vulnerabilidade, escreva para <security@telecomtowerpower.com.br> ou abra um *advisory* privado em [github.com/danielnovaisantunes/TELECOM-TOWER-POWER/security/advisories](https://github.com/danielnovaisantunes/TELECOM-TOWER-POWER/security/advisories).

## Criptografia

| Camada | Implementação |
|---|---|
| Em trânsito | TLS 1.2+ no ALB AWS (sa-east-1) e Caddy edge; HSTS habilitado em todos os domínios `*.telecomtowerpower.com.br` |
| Em repouso (Postgres) | RDS storage encryption (AES-256, KMS gerenciado pela AWS) |
| Em repouso (S3) | SSE-S3 (AES-256) em todos os buckets de relatórios PDF e backups |
| Em repouso (Redis) | ElastiCache encryption-at-rest, auth token rotacionado mensalmente |
| Segredos | AWS SSM Parameter Store (SecureString, KMS); nunca committados; sincronizados via GitHub Actions OIDC |

## Autenticação e autorização

- API keys por tenant (`verify_api_key`), com fallback Bearer JWT para SSO
- SSO/OIDC via AWS Cognito (Hosted UI + troca de código server-side) nos planos Business+
- SAML 2.0 disponível em Ultra
- IDOR mitigado: toda query filtra por `tenant_id` antes de retornar
- Audit log: cada ação tenant é registrada (`/tenant/audit`), retenção 90 dias

## Retenção e segregação de dados

- **Dados de torres** (140.498 registros ANATEL + OpenCelliD): públicos, não sensíveis
- **Logs de API**: retidos 30 dias, depois purgados
- **Audit log**: 90 dias
- **Backups Postgres**: 14 dias em S3 com lifecycle policy
- **Dados de uso por tenant**: isolados em `tenant_id` em todas as tabelas; sem cross-tenant queries
- **Não coletamos** dados pessoais de usuários finais. Os "receivers" em batch reports são endereços/coordenadas, não pessoas

## OWASP Top 10 — controles ativos

| Risco | Mitigação |
|---|---|
| Injection | SQLAlchemy parametrizado em 100% das queries; nenhum f-string SQL |
| Broken access control / IDOR | Filtro `tenant_id` obrigatório em handlers; verificado em testes de integração |
| Cryptographic failures | TLS 1.2+ everywhere; bcrypt para hashes; rotação trimestral de chaves |
| Insecure design | Rate limiting per-IP no signup; per-tenant nas rotas autenticadas |
| Security misconfiguration | Cabeçalhos de segurança no Caddy; CORS restritivo; CSP em `/portal` |
| Vulnerable components | `pip-audit` + Dependabot semanal; CI bloqueia merge em high/critical |
| Auth failures | Lockout após 5 tentativas; logging de signin via `audit_log` |
| Data integrity | Webhooks Stripe verificados por assinatura; SSO state validado |
| Logging & monitoring | Prometheus + Loki + Tempo; 12 alert rules; PagerDuty critical-only |
| SSRF | Allowlist de hosts em `/bedrock/*`; URL validation no signup |

## Backups e continuidade

- Postgres + Grafana → S3 (sa-east-1) todas as noites, 14 dias de retenção
- **Restore drill verificado** toda segunda 07:15 UTC: container Postgres efêmero, assertions de row-count em `towers`, `api_keys`, `alembic_version`
- Workflow: [`backup-restore-drill.yml`](https://github.com/danielnovaisantunes/TELECOM-TOWER-POWER/blob/main/.github/workflows/backup-restore-drill.yml)
- Detalhes de RTO/RPO: ver [Confiabilidade](reliability.md)

## Sub-processadores

| Vendor | Função | Região |
|---|---|---|
| Amazon Web Services | ECS, RDS, ElastiCache, S3, SQS, Lambda, KMS, SSM, Cognito | sa-east-1 (São Paulo) |
| Cloudflare | CDN, WAF, Turnstile (anti-abuse no signup) | Edge global |
| Stripe | Cobrança e webhooks | EU + US (PCI DSS Level 1) |
| Railway | Failover quente para `web`, `frontend`, `worker` | us-west |
| GitHub | Repositório, Actions, container registry | US |

Lista completa e DPA disponíveis sob NDA para clientes Business+.

## Compliance

- **LGPD**: política de privacidade pública em [Privacidade](privacy.md); DPO designado; processo de exercício de direitos via <privacy@telecomtowerpower.com.br>
- **SOC 2**: framework de controles documentado em [Compliance/SOC 2](../compliance/soc2/README.md). Auditoria Type I em planejamento para 2026-Q4. Não somos certificados ainda — o framework é interno e auditável.
- **ISO 27001**: não certificados. Não há plano de curto prazo. Práticas alinhadas ao Anexo A documentadas nas políticas SOC 2.
- **PCI DSS**: não armazenamos dados de cartão. Stripe é o processador (PCI Level 1).

## Endurecimento recente (2026-Q2)

Mudanças concretas em produção neste trimestre:

- **Snap ANATEL por prestadora** ([`snap_anatel.py`](https://github.com/danielnovaisantunes/TELECOM-TOWER-POWER/blob/main/snap_anatel.py)): coordenadas de torres alinhadas por SMP/SME individual (Vivo, Claro, TIM, Algar, Sercomtel etc.), em vez de centroide agregado. Reduz erro de localização em áreas urbanas densas.
- **Modelo ML retrainado contra RSSI real**: ridge-v1 com 17 features (SRTM, Fresnel, terrain roughness), RMSE de 12.94 dB em n=20 000 amostras, supera baseline Hata físico-puro. Retrain noturno em CI, hot-pull do S3 no boot.
- **Fila prioritária Enterprise**: SQS dedicado + Lambda consumer; SLA 99.95% no tier Ultra; lote de até 50 000 receptores.
- **Hardening LGPD/OWASP**: rate limit per-IP no signup (parser tolerante a `unlimited|0|off`), filtro `tenant_id` obrigatório em handlers (mitigação IDOR), audit log em todas as ações tenant.

**Limite honesto**: 100% dos dados são públicos (ANATEL/OpenCelliD) e o stack é AWS externo. Esta é uma escolha consciente — torna o produto inadequado para operações Tier-1 que exigem segregação on-premises de dados de engenharia, e adequado para WISPs / ISPs regionais / consultorias RF que querem pagar pelo dado já curado em vez de subir um Atoll.

## Vulnerability disclosure

- Email: <security@telecomtowerpower.com.br>
- GPG: chave pública em [/.well-known/security.txt](https://api.telecomtowerpower.com.br/.well-known/security.txt)
- SLA inicial de resposta: 48 h úteis
- Não rodamos bug bounty pago atualmente; oferecemos *Hall of Fame* público para divulgações responsáveis

## Limitações conhecidas

Honestidade técnica:

- **Bus factor**: equipe pequena. Mitigação: código em escrow contratual nos planos Enterprise/Ultra; runbook público em [Operações/Runbook](../operations/runbook.md); backups automatizados verificados semanalmente.
- **Sem certificações externas hoje** (SOC 2, ISO 27001). Compensamos com transparência: políticas, runbooks, drills e arquitetura são públicos.
- **Região única** (sa-east-1). Failover quente para Railway us-west cobre indisponibilidade de zona AWS, não de região global.
- **Dados em AWS externo**: por design, todos os cálculos rodam em AWS sa-east-1 (não no perímetro do cliente). Para operações Tier-1 com requisito de segregação total de dados de planejamento RF, esta arquitetura é inadequada — use Atoll, Planet ou CelPlan on-premise. Nosso ICP (WISPs, ISPs regionais, consultoria RF) tipicamente não tem essa restrição.

## Hardening recente (2026-Q2)

| Item | Detalhes |
|---|---|
| Snapping ANATEL por prestadora | `snap_anatel.py` recalcula coordenadas por SMP/SME provider, melhorando precisão por operadora em áreas urbanas densas |
| Modelo ML retreinado com RSSI real | ridge-v1, RMSE 12.94 dB, n=20.000 amostras, 17 features (SRTM, Fresnel ratio, terrain roughness); supera baseline Hata físico-puro |
| Fila prioritária Enterprise | SQS dedicada + Lambda consumer com SLA 99.95% para batch reports |
| Rate limiting per-IP no signup | proteção anti-abuse antes de Turnstile; configurável via `SIGNUP_FREE_RATE_LIMIT_PER_HOUR` |
| Audit + restore drill semanal | container Postgres efêmero, assertions de row-count em `towers`, `api_keys`, `alembic_version` |
