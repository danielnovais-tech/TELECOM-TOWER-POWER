import boto3, os
client = boto3.client("bedrock", region_name="us-east-1")
try:
    response = client.list_foundation_models()
    print("Models found:", len(response.get('modelSummaries', [])))
    for m in response.get('modelSummaries', []):
        if 'anthropic' in m['modelId'].lower():
            print(f"ID: {m['modelId']} - {m['modelName']}")
except Exception as e:
    print("ERROR listing models:", type(e).__name__, str(e))
