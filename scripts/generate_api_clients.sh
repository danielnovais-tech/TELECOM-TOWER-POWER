#!/usr/bin/env bash
# Regenerate typed API clients from the FastAPI OpenAPI schema.
# Works from any directory.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "── Exporting OpenAPI schema ──"
python scripts/export_openapi.py

# Load nvm if available
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

echo "── Generating TypeScript types (frontend) ──"
cd "$ROOT/frontend"
npx openapi-typescript openapi.json -o src/api-schema.d.ts

echo "── Generating Python SDK ──"
cd "$ROOT"
if command -v openapi-python-client &>/dev/null; then
  openapi-python-client generate \
    --path openapi.json \
    --output-path sdks/python \
    --config sdk-config.yml \
    --meta setup \
    --overwrite
  echo "  ✓ sdks/python/"
else
  echo "  ⚠ openapi-python-client not installed – skipping (pip install openapi-python-client)"
fi

echo "── Generating JavaScript/TypeScript SDK ──"
if [ -d "$ROOT/sdks/javascript/node_modules/openapi-typescript-codegen" ]; then
  cd "$ROOT/sdks/javascript"
  npx openapi --input ../../openapi.json --output src --client fetch --name TelecomTowerPowerClient
  echo "  ✓ sdks/javascript/src/"
else
  echo "  ⚠ openapi-typescript-codegen not installed – skipping (cd sdks/javascript && npm install)"
fi

echo "── Done ──"
