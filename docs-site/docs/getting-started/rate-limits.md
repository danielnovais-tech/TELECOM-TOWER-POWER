# Limites de taxa

Cada chave tem uma janela deslizante de 60 segundos. Ao ser excedida a API devolve `429 Too Many Requests`.

| Plano | Req/min | Chamadas/mês | Lote (receptores) |
|---|---:|---:|---:|
| Free | 10 | 200 | — |
| Starter | 30 | 3.000 | 100 |
| Pro | 100 | 25.000 | 500 |
| Business | 300 | 150.000 | 2.000 |
| Enterprise | 1.000 | customizado | 10.000 |
| Ultra | 5.000 | customizado | 50.000 |

## Headers de resposta

- `X-RateLimit-Limit` — teto de req/min vigente
- `X-RateLimit-Remaining` — quantas ainda cabem na janela
- `Retry-After` — presente em respostas 429

## Chaves de demo

Cap adicional de **6 req/min** independente do tier nominal da chave.
