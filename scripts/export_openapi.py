#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema to static JSON files.

Usage:
    python scripts/export_openapi.py

Outputs:
    frontend/openapi.json   – consumed by openapi-typescript for the React client
    openapi.json            – consumed by openapi-python-client for the Streamlit client
"""

import json
import sys
import os

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telecom_tower_power_api import app

schema = app.openapi()

# Write to frontend/ for TypeScript generation
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "openapi.json")
with open(frontend_path, "w") as f:
    json.dump(schema, f, indent=2)
print(f"✓ Wrote {os.path.relpath(frontend_path)}")

# Write to project root for Python client generation
root_path = os.path.join(os.path.dirname(__file__), "..", "openapi.json")
with open(root_path, "w") as f:
    json.dump(schema, f, indent=2)
print(f"✓ Wrote {os.path.relpath(root_path)}")
