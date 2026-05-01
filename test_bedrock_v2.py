# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
import os, sys, boto3
client = boto3.client("bedrock-runtime", region_name="us-east-1")
# Trying with one of the specific IDs listed earlier
model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
print(f"Testing model: {model_id}")
try:
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "Write a one-sentence bedtime story about a unicorn."}]}]
    )
    print("SUCCESS:", response["output"]["message"]["content"][0]["text"])
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
