import boto3
client = boto3.client("bedrock-runtime", region_name="us-east-1")
model_id = "amazon.nova-micro-v1:0"
print(f"Testing model: {model_id}")
try:
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": "Say hi."}]}]
    )
    print("SUCCESS:", response["output"]["message"]["content"][0]["text"])
except Exception as e:
    print("ERROR:", type(e).__name__, str(e))
