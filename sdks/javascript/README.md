# telecom-tower-power-client (JavaScript/TypeScript)

Auto-generated TypeScript SDK for the **TELECOM TOWER POWER API**.

## Installation

```bash
npm install telecom-tower-power-client
# or copy this directory into your project
```

## Usage

```typescript
import { TelecomTowerPowerClient } from "telecom-tower-power-client";

const client = new TelecomTowerPowerClient({
  BASE: "https://api.telecomtowerpower.com.br",
  HEADERS: { "X-API-Key": "your-api-key" },
});

// List towers
const towers = await client.default.listTowersTowersGet();

// Analyze a link
const result = await client.default.analyzeLinkAnalyzePost("tower-001", {
  lat: -23.55,
  lon: -46.63,
  height_m: 10,
  antenna_gain_dbi: 12,
});

// Portal – account info
const profile = await client.default.portalProfilePortalProfileGet();
const usage = await client.default.portalUsagePortalUsageGet();
const jobs = await client.default.portalJobsPortalJobsGet(20);
const billing = await client.default.portalBillingPortalBillingGet();
```

## Regenerating

After API changes, regenerate the SDK:

```bash
# From project root
bash scripts/generate_api_clients.sh

# Or from this directory
npm run generate
```

## Models

All request/response models are in `src/models/`:

- `TowerInput`, `ReceiverInput`, `LinkAnalysisResponse`
- `Band`, `SignupRequest`, `CheckoutRequest`
- `BedrockChatRequest`, `PrefetchRequest`
- `HTTPValidationError`, `ValidationError`
