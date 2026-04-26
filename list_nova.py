import boto3
client = boto3.client("bedrock", region_name="us-east-1")
try:
    response = client.list_foundation_models()
    for m in response.get('modelSummaries', []):
        if 'nova' in m['modelId'].lower():
            print(f"ID: {m['modelId']}")
except Exception as e:
    print("ERROR:", str(e))
