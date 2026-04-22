# Quickstart

## 1. Obtenha uma chave de API

Para testar agora mesmo use uma chave pública de demo (10 req/min, sem PDF, sem IA):

```
X-API-Key: demo_ttp_free_2604
```

Para produção, [crie uma conta](https://app.telecomtowerpower.com.br/signup) e escolha um plano.

## 2. Faça sua primeira chamada

```bash
curl -X POST https://api.telecomtowerpower.com.br/analyze_link \
  -H "X-API-Key: demo_ttp_free_2604" \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": {"lat": -15.78, "lon": -47.93, "height": 10},
    "frequency_mhz": 1800
  }'
```

## 3. Interprete o resultado

A API retorna a melhor torre candidata com `link_margin_db`. Valores acima de **10 dB** geralmente indicam enlace viável em céu claro; acima de **20 dB** suporta chuva intensa nas bandas 700/1800/2100 MHz.

## 4. Próximos passos

- Veja a [referência da API](../api/overview.md) para todos os endpoints disponíveis.
- Configure [autenticação](authentication.md) para produção.
- Leia sobre [limites de taxa](rate-limits.md) antes de colocar em produção.
