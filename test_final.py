# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
import os, boto3, requests
token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
# Use exact ID from list_models.py
model_id = "anthropic.claude-haiku-4-5-20251001-v1:0"
url = f"https://bedrock-runtime.us-east-1.amazonaws.com/model/{model_id}/converse"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
data = {
    "messages": [{"role": "user", "content": [{"text": "Write a one-sentence story about a unicorn."}]}]
}
response = requests.post(url, headers=headers, json=data)
print(f"Model: {model_id}")
print(f"Status: {response.status_code}")
print(f"Body: {response.text}")
