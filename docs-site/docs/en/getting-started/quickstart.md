# Quickstart

## 1. Get an API key

Try it now with the public demo key (6 rpm, no PDF, no AI):

```
X-API-Key: demo_try_free_2026_04
```

For production, [sign up](https://app.telecomtowerpower.com.br/signup) and pick a plan.

## 2. First call

```bash
curl -X POST https://api.telecomtowerpower.com.br/analyze_link \
  -H "X-API-Key: demo_try_free_2026_04" \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": {"lat": -15.78, "lon": -47.93, "height": 10},
    "frequency_mhz": 1800
  }'
```

## 3. Read the result

The API returns the best candidate tower with `link_margin_db`. Values above **10 dB** usually mean the link is viable in clear sky; above **20 dB** tolerates heavy rain in the 700 / 1800 / 2100 MHz bands.

## 4. Next

- Try the [multi-hop repeater](../api/repeaters.md) endpoint for NLOS scenarios.
- Check the [Python SDK](../sdks/python.md) to embed it in scripts.
- Read about [rate limits](rate-limits.md) before going to production.
