import os, sys, boto3
tok = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
print(f"token_len={len(tok)}")
if not tok:
    print("ERROR: token not set in env")
    sys.exit(1)

client = boto3.client("bedrock-runtime", region_name="us-east-1")

models = ["us.anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic.claude-haiku-4-5-20251001-v1:0"]

for model_id in models:
    print(f"\nTesting model: {model_id}")
    try:
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Write a one-sentence bedtime story about a unicorn."}]}]
        )
        print("SUCCESS:", response["output"]["message"]["content"][0]["text"])
    except Exception as e:
        print("ERROR:", type(e).__name__, str(e))
