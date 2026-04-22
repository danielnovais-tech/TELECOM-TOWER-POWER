# Visão geral da API

Base URL: `https://api.telecomtowerpower.com.br`

OpenAPI: [/openapi.json](https://api.telecomtowerpower.com.br/openapi.json) · Swagger UI: [/docs](https://api.telecomtowerpower.com.br/docs)

## Endpoints principais

| Método | Caminho | Propósito |
|---|---|---|
| POST | `/analyze_link` | Analisa um receptor contra todas as torres no raio |
| POST | `/plan_repeater` | Encontra cadeia multi-saltos para cenário NLOS |
| POST | `/plan_repeater/async` | Versão assíncrona com job id |
| GET  | `/job/{id}` | Status e resultado de job assíncrono |
| POST | `/batch` | Processa CSV de receptores (tier Pro+) |
| GET  | `/towers` | Lista/filtra torres por bbox, operadora, banda |
| POST | `/ai/explain_link` | Explicação em linguagem natural (tier Pro+) |
| GET  | `/report/{id}.pdf` | PDF do relatório (tier Starter+) |
| GET  | `/health` | Healthcheck |

Detalhes de cada endpoint nas próximas páginas.
