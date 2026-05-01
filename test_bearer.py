# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
import os, boto3, requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
url = "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-5-haiku-20241022-v1:0/converse"
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
data = {
    "messages": [{"role": "user", "content": [{"text": "Hi"}]}]
}

print(f"Token length: {len(token) if token else 0}")
try:
    response = requests.post(url, headers=headers, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
