# API overview

Base URL: `https://api.telecomtowerpower.com.br`

OpenAPI: [/openapi.json](https://api.telecomtowerpower.com.br/openapi.json) · Swagger UI: [/docs](https://api.telecomtowerpower.com.br/docs)

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/analyze_link` | Analyze a receiver against every tower in range |
| POST | `/plan_repeater` | Find multi-hop chain for NLOS scenarios |
| POST | `/plan_repeater/async` | Async variant with job id |
| POST | `/coverage/predict` | Terrain-aware ML signal/coverage prediction (Pro+ tier) |
| GET  | `/job/{id}` | Status + result of an async job |
| POST | `/batch` | Process a CSV of receivers (Pro+ tier) |
| GET  | `/towers` | List/filter towers by bbox, operator, band |
| POST | `/ai/explain_link` | Natural-language explanation (Pro+ tier) |
| GET  | `/report/{id}.pdf` | PDF report (Starter+ tier) |
| GET  | `/health` | Healthcheck |

See the next pages for per-endpoint details.
