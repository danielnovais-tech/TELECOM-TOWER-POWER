# Autenticação

Todas as requisições exigem o header `X-API-Key`. As chaves são criadas no [portal](https://app.telecomtowerpower.com.br/portal) após a assinatura de um plano.

## Chaves de demo

Chaves públicas rotacionadas mensalmente — ideais para avaliar a API antes de assinar.

- Prefixo: `demo_try_…`
- Taxa: **6 req/min** (cap forte, independente do tier)
- Sem exportação PDF, sem IA
- Respostas carregam o header `X-Demo-Key: true`

## Chaves de produção

- Prefixo: `ttp_` seguido de 32 caracteres aleatórios (`secrets.token_urlsafe(32)`)
- Entregues via email no momento do checkout
- Persistentes: não rotacionam automaticamente — gire manualmente via portal se suspeitar de vazamento

## Boas práticas

- **Nunca** comprometa uma chave no front-end. Use um proxy backend.
- Configure `IP allowlist` no plano Enterprise.
- Monitore o header `X-RateLimit-Remaining` antes de parar de receber `429`.
