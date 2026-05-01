# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
import os, sys, boto3, json
client = boto3.client("bedrock-runtime", region_name="us-east-1")
model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
print(f"Testing model with invoke_model: {model_id}")
body = json.dumps({
    "anthropic_version": "bedrock-2023-05-31",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Write a one-sentence story about a unicorn."}]
})
try:
    response = client.invoke_model(modelId=model_id, body=body)
    response_body = json.loads(response.get('body').read())
    print("SUCCESS:", response_body['content'][0]['text'])
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
