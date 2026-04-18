"""
bedrock_service.py – Amazon Bedrock integration for the AI Playground.

Uses a base foundation model from the Amazon Bedrock playground to provide
telecom-domain AI assistance: coverage analysis interpretation, link budget
recommendations, RF planning guidance, and general telecom Q&A.

Environment variables:
    BEDROCK_REGION       – AWS region for Bedrock (default: us-east-1)
    BEDROCK_MODEL_ID     – Foundation model ID (default: amazon.nova-micro-v1:0)
    BEDROCK_MAX_TOKENS   – Max tokens in response (default: 1024)
    BEDROCK_TEMPERATURE  – Sampling temperature 0-1 (default: 0.7)
"""

import json
import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "1024"))
BEDROCK_TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0.7"))

_client = None

SYSTEM_PROMPT = (
    "You are an expert telecom RF engineer assistant for the TELECOM TOWER POWER "
    "platform. You help users understand link budget analysis, RF propagation, "
    "Fresnel zone clearance, repeater chain planning, antenna specifications, "
    "and general cellular network topics. Keep answers concise and technically "
    "accurate. When referencing signal levels, use dBm. When referencing distances, "
    "use km. If asked about something outside telecom engineering, politely redirect "
    "the conversation."
)


def _get_client():
    """Lazy-init Bedrock Runtime client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
        )
    return _client


def _build_titan_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Amazon Titan Text models."""
    return {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
            "stopSequences": [],
        },
    }


def _build_claude_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Anthropic Claude models on Bedrock."""
    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }


def _build_llama_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Meta Llama models on Bedrock."""
    return {
        "prompt": f"<s>[INST] <<SYS>>\n{SYSTEM_PROMPT}\n<</SYS>>\n\n{prompt} [/INST]",
        "max_gen_len": max_tokens,
        "temperature": temperature,
        "top_p": 0.9,
    }


def _build_nova_body(prompt: str, max_tokens: int, temperature: float) -> dict:
    """Build request body for Amazon Nova models on Bedrock."""
    return {
        "schemaVersion": "messages-v1",
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "maxNewTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.9,
        },
    }


def _build_request_body(
    model_id: str, prompt: str, max_tokens: int, temperature: float
) -> dict:
    """Route to the correct body builder based on model family."""
    mid = model_id.lower()
    if "claude" in mid or "anthropic" in mid:
        return _build_claude_body(prompt, max_tokens, temperature)
    if "llama" in mid or "meta" in mid:
        return _build_llama_body(prompt, max_tokens, temperature)
    if "nova" in mid:
        return _build_nova_body(prompt, max_tokens, temperature)
    # Default: Titan
    full_prompt = f"{SYSTEM_PROMPT}\n\nUser: {prompt}\n\nAssistant:"
    return _build_titan_body(full_prompt, max_tokens, temperature)


def _extract_response_text(model_id: str, response_body: dict) -> str:
    """Extract generated text from model-specific response format."""
    mid = model_id.lower()
    if "claude" in mid or "anthropic" in mid:
        # Messages API format
        content = response_body.get("content", [])
        return "".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
    if "llama" in mid or "meta" in mid:
        return response_body.get("generation", "")
    if "nova" in mid:
        # Nova uses messages-v1 response format
        output = response_body.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])
        return "".join(block.get("text", "") for block in content)
    # Titan
    results = response_body.get("results", [])
    if results:
        return results[0].get("outputText", "")
    return response_body.get("outputText", "")


def invoke_model(
    prompt: str,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    context: Optional[str] = None,
) -> dict:
    """
    Invoke a Bedrock foundation model with the given prompt.

    Args:
        prompt: User prompt / question.
        model_id: Override the default model ID.
        max_tokens: Override the default max tokens.
        temperature: Override the default temperature.
        context: Optional analysis context (e.g. JSON of a link analysis result)
                 that gets prepended to the prompt.

    Returns:
        dict with keys: response, model_id, input_tokens (estimate), output_tokens (estimate)
    """
    model = model_id or BEDROCK_MODEL_ID
    tokens = max_tokens or BEDROCK_MAX_TOKENS
    temp = temperature if temperature is not None else BEDROCK_TEMPERATURE

    full_prompt = prompt
    if context:
        full_prompt = (
            f"Here is the current analysis context:\n```json\n{context}\n```\n\n"
            f"{prompt}"
        )

    body = _build_request_body(model, full_prompt, tokens, temp)

    client = _get_client()
    try:
        response = client.invoke_model(
            modelId=model,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        logger.error("Bedrock invoke_model error: %s – %s", error_code, error_msg)
        raise

    response_body = json.loads(response["body"].read())
    text = _extract_response_text(model, response_body)

    # Token estimates from response metadata when available
    usage = response_body.get("usage", {})
    input_tokens = usage.get("input_tokens") or usage.get("inputTextTokenCount") or 0
    output_tokens = usage.get("output_tokens") or usage.get("totalTokenCount") or 0

    return {
        "response": text.strip(),
        "model_id": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def list_available_models() -> list[dict]:
    """
    Return a curated list of Bedrock base models suitable for the playground.
    Queries the Bedrock control-plane API for available text models.
    Falls back to a static list if the API call fails.
    """
    static_models = [
        {
            "model_id": "amazon.nova-micro-v1:0",
            "provider": "Amazon",
            "name": "Nova Micro",
        },
        {
            "model_id": "amazon.nova-lite-v1:0",
            "provider": "Amazon",
            "name": "Nova Lite",
        },
        {
            "model_id": "amazon.nova-pro-v1:0",
            "provider": "Amazon",
            "name": "Nova Pro",
        },
        {
            "model_id": "anthropic.claude-haiku-4-5-20251001-v1:0",
            "provider": "Anthropic",
            "name": "Claude Haiku 4.5",
        },
        {
            "model_id": "anthropic.claude-sonnet-4-20250514-v1:0",
            "provider": "Anthropic",
            "name": "Claude Sonnet 4",
        },
    ]

    try:
        bedrock_ctrl = boto3.client("bedrock", region_name=BEDROCK_REGION)
        resp = bedrock_ctrl.list_foundation_models(
            byOutputModality="TEXT",
            byInferenceType="ON_DEMAND",
        )
        models = []
        for m in resp.get("modelSummaries", []):
            models.append(
                {
                    "model_id": m["modelId"],
                    "provider": m.get("providerName", "Unknown"),
                    "name": m.get("modelName", m["modelId"]),
                }
            )
        return models if models else static_models
    except Exception:
        logger.info("Could not list Bedrock models; returning static list")
        return static_models
