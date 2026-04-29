# TELECOM TOWER POWER

**Radio link planning API backed by live ANATEL data.**

[![Try the API](https://img.shields.io/badge/try-API-0369a1?style=for-the-badge)](https://app.telecomtowerpower.com.br/signup)

## What it is

A REST API that answers the question every ISP, RF consultant and carrier asks every day:

> *"Is there a tower that can serve this address, with line of sight and enough link margin?"*

We return the answer in milliseconds by querying 140,498 geolocated towers (ANATEL + OpenCelliD), cross-referencing SRTM 90m terrain, and — optionally — producing a natural-language technical statement via AI.

## In 30 seconds

```bash
curl -X POST https://api.telecomtowerpower.com.br/analyze_link \
  -H "X-API-Key: demo_ttp_free_2604" \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": {"lat": -20.5, "lon": -41.9, "height": 5},
    "frequency_mhz": 700,
    "max_distance_km": 30
  }'
```

Trimmed response:

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

## Next

- [Quickstart](getting-started/quickstart.md) — first call in 5 minutes
- [Authentication](getting-started/authentication.md) — how to get and use an API key
- [API reference](api/overview.md) — every endpoint
