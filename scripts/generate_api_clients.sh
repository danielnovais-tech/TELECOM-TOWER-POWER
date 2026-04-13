#!/usr/bin/env bash
# Regenerate typed API clients from the FastAPI OpenAPI schema.
# Works from any directory.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "── Exporting OpenAPI schema ──"
python scripts/export_openapi.py

echo "── Generating TypeScript types ──"
cd frontend

# Load nvm if available
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

npx openapi-typescript openapi.json -o src/api-schema.d.ts

echo "── Done ──"
