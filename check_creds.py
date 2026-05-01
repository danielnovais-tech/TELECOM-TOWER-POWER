# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
import boto3
session = boto3.Session()
creds = session.get_credentials()
print(f"Access Key: {creds.access_key[:5]}...{creds.access_key[-5:] if creds.access_key else ''}")
print(f"Token present: {bool(creds.token)}")
# Try a simple GET request
client = boto3.client("sts")
try:
    print("Identity:", client.get_caller_identity()['Arn'])
except Exception as e:
    print("STS Error:", str(e))
