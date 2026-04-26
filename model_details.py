import boto3
client = boto3.client("bedrock", region_name="us-east-1")
model_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
try:
    print(f"Model ID: {model_id}")
    resp = client.get_foundation_model(modelIdentifier=model_id)
    print("Model Details:", resp['modelDetails'])
except Exception as e:
    print("ERROR:", str(e))
