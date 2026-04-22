# TELECOM TOWER POWER

**API de planejamento de enlaces de rádio com dados ANATEL em tempo real.**

[![Teste a API](https://img.shields.io/badge/teste-API-0369a1?style=for-the-badge)](https://app.telecomtowerpower.com.br/signup)

## O que é

Uma API REST que responde à pergunta que todo ISP, consultor RF e operadora se faz todo dia:

> *"Existe uma torre que possa atender este endereço, com linha de visada e margem de enlace suficiente?"*

Entregamos a resposta em milissegundos, consultando 140.906 torres georreferenciadas (ANATEL + OpenCelliD), cruzando com relevo SRTM de 90 m e — opcionalmente — produzindo uma análise técnica em linguagem natural via IA.

## Em 30 segundos

```bash
curl -X POST https://api.telecomtowerpower.com.br/analyze_link \
  -H "X-API-Key: demo_try_free_2026_04" \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": {"lat": -20.5, "lon": -41.9, "height": 5},
    "frequency_mhz": 700,
    "max_distance_km": 30
  }'
```

Resposta resumida:

```json
{
  "best_tower": {
    "operator": "VIVO",
    "distance_km": 6.8,
    "azimuth_deg": 142,
    "los_clear": true,
    "fresnel_ok": true,
    "path_loss_db": 106.2,
    "link_margin_db": 18.3
  },
  "alternatives": [ ... 5 more towers ... ]
}
```

## Próximos passos

- [Quickstart](getting-started/quickstart.md) — primeira chamada em 5 minutos
- [Autenticação](getting-started/authentication.md) — como obter e usar uma chave de API
- [Referência da API](api/overview.md) — todos os endpoints
