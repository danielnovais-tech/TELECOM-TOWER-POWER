# Quickstart

## 1. Get an API key

Try it now with a public demo key (10 rpm, no PDF, no AI):

```
X-API-Key: demo_ttp_free_2604
```

For production, [sign up](https://app.telecomtowerpower.com.br/signup) and pick a plan.

## 2. First call

```bash
curl -X POST https://api.telecomtowerpower.com.br/analyze_link \
  -H "X-API-Key: demo_ttp_free_2604" \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": {"lat": -15.78, "lon": -47.93, "height": 10},
    "frequency_mhz": 1800
  }'
```

## 3. Read the result

The API returns the best candidate tower with `link_margin_db`. Values above **10 dB** usually mean the link is viable in clear sky; above **20 dB** tolerates heavy rain in the 700 / 1800 / 2100 MHz bands.

## 4. Next

- Browse the [API reference](../api/overview.md) for every available endpoint.
- Set up [authentication](authentication.md) for production.
- Read about [rate limits](rate-limits.md) before going to production.
