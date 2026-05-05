# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
rf_engines.llm_engine — Local Llama-3 / Ollama backend for the AI Playground.

Drop-in replacement for ``bedrock_service`` that talks to a local
Ollama daemon (https://ollama.com/) instead of Amazon Bedrock. The
public surface (``invoke_model``, ``list_available_models``,
``compare_scenarios``, ``analyze_batch``, ``suggest_antenna_height``)
is identical so the API layer can swap providers via the
``LLM_PROVIDER`` env var without further code changes.

Why local Ollama?
-----------------
* Air-gapped / on-prem installs: no AWS dependency, no per-token cost.
* Privacy: telecom planning context (tower coords, customer batches)
  never leaves the operator's network.
* Determinism: same model weight = same output across deployments.

Environment variables:
    OLLAMA_BASE_URL    – Ollama HTTP endpoint (default: http://localhost:11434)
    OLLAMA_MODEL       – Model tag (default: llama3)
    OLLAMA_MAX_TOKENS  – Max tokens in response (default: 1024)
    OLLAMA_TEMPERATURE – Sampling temperature (default: 0.7)
    OLLAMA_TIMEOUT_S   – HTTP timeout in seconds (default: 120)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

# Re-use the prompt-injection guards, RAG retriever and analysis-context
# enricher from bedrock_service so we maintain a SINGLE source of truth
# for the telecom-domain reasoning logic. Only the transport layer is
# different between the two backends.
from bedrock_service import (  # noqa: E402  (sibling top-level module)
    SYSTEM_PROMPT,
    _build_analysis_context,
    _looks_like_injection,
    _retrieve_rag_context,
    _wrap_user_input,
)

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_MAX_TOKENS = int(os.getenv("OLLAMA_MAX_TOKENS", "1024"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.7"))
OLLAMA_TIMEOUT_S = float(os.getenv("OLLAMA_TIMEOUT_S", "120"))

# ── Public constants ──────────────────────────────────────────────
PROVIDER_NAME = "ollama"


# ── Internals ─────────────────────────────────────────────────────


def _post_chat(
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """POST to Ollama's /api/chat endpoint and return the parsed JSON.

    Uses ``stream=false`` so the response is a single JSON document — the
    operator can switch to streaming later if the UI needs token-by-token
    rendering, but for the current FastAPI handlers a single round-trip
    is simpler.
    """
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": temperature,
            # Llama-3 / Ollama uses ``num_predict`` (= max new tokens).
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }
    resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def _extract_response_text(body: Dict[str, Any]) -> str:
    """Pull the assistant text out of an Ollama /api/chat response."""
    msg = body.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    # Fallback for /api/generate-style responses, in case the daemon
    # returns the legacy schema.
    legacy = body.get("response")
    if isinstance(legacy, str):
        return legacy.strip()
    return ""


def _token_counts(body: Dict[str, Any]) -> tuple[int, int]:
    """Best-effort token usage extraction.

    Ollama reports ``prompt_eval_count`` (input) and ``eval_count``
    (output). Older builds omit one or both — return zeros in that case
    so the caller sees a stable schema.
    """
    inp = int(body.get("prompt_eval_count") or 0)
    out = int(body.get("eval_count") or 0)
    return inp, out


# ── Public API ────────────────────────────────────────────────────


def invoke_model(
    prompt: str,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    context: Optional[str] = None,
) -> dict:
    """Invoke the local Llama-3 model with prompt-injection guards + RAG.

    Mirrors :func:`bedrock_service.invoke_model` exactly so the
    AI-Playground endpoints in ``telecom_tower_power_api.py`` can swap
    providers through ``LLM_PROVIDER`` without further changes.

    Returns a dict with keys: ``response``, ``model_id``,
    ``input_tokens``, ``output_tokens`` (and optionally ``refused`` /
    ``offline``).
    """
    model = model_id or OLLAMA_MODEL
    tokens = max_tokens or OLLAMA_MAX_TOKENS
    temp = temperature if temperature is not None else OLLAMA_TEMPERATURE

    # Prompt-injection guard (same regex as Bedrock path).
    inj = _looks_like_injection(prompt) or (
        _looks_like_injection(context) if context else None
    )
    if inj:
        logger.warning("ollama: rejected prompt-injection attempt (matched %r)", inj)
        return {
            "response": (
                "I can't follow embedded instructions to override my role. "
                "If you have a telecom RF engineering question, please "
                "rephrase it without the meta-instructions and I'll be "
                "happy to help."
            ),
            "model_id": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "refused": True,
        }

    # Build RAG-augmented prompt (same retriever + enricher as Bedrock).
    rag_context = _retrieve_rag_context(prompt)
    wrapped = _wrap_user_input(prompt)
    if context:
        enriched = _build_analysis_context(context)
        full_user = (
            f"{enriched}\n\n{rag_context}\nUser question: {wrapped}"
        )
    elif rag_context:
        full_user = f"{rag_context}\nUser question: {wrapped}"
    else:
        full_user = wrapped

    try:
        body = _post_chat(
            model=model,
            system=SYSTEM_PROMPT,
            user=full_user,
            max_tokens=tokens,
            temperature=temp,
        )
    except requests.exceptions.ConnectionError as e:
        logger.error("ollama: connection refused at %s (%s)", OLLAMA_BASE_URL, e)
        return {
            "response": (
                f"AI Playground (local) is unavailable: cannot reach the "
                f"Ollama daemon at {OLLAMA_BASE_URL}. Start it with "
                f"'ollama serve' or set OLLAMA_BASE_URL to a reachable host."
            ),
            "model_id": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "offline": True,
        }
    except requests.exceptions.HTTPError as e:
        logger.error("ollama: HTTP %s from %s — %s", e.response.status_code, OLLAMA_BASE_URL, e)
        raise
    except requests.exceptions.Timeout:
        logger.error("ollama: timeout after %ss talking to %s", OLLAMA_TIMEOUT_S, OLLAMA_BASE_URL)
        return {
            "response": (
                f"AI Playground (local) timed out after {OLLAMA_TIMEOUT_S:.0f}s. "
                "The model may still be loading on first call — retry in a moment."
            ),
            "model_id": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "timeout": True,
        }

    text = _extract_response_text(body)
    in_tok, out_tok = _token_counts(body)

    return {
        "response": text,
        "model_id": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


def list_available_models() -> List[Dict[str, Any]]:
    """Return models pulled into the local Ollama daemon.

    Falls back to a curated static list if the daemon is unreachable so
    the AI-Playground UI can still render selectable options.
    """
    static_models: List[Dict[str, Any]] = [
        {"model_id": "llama3", "provider": "Meta (local)", "name": "Llama 3 8B"},
        {"model_id": "llama3:70b", "provider": "Meta (local)", "name": "Llama 3 70B"},
        {"model_id": "llama3.1", "provider": "Meta (local)", "name": "Llama 3.1 8B"},
        {"model_id": "llama3.2", "provider": "Meta (local)", "name": "Llama 3.2 3B"},
    ]
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        body = resp.json()
        models: List[Dict[str, Any]] = []
        for m in body.get("models", []):
            tag = m.get("name") or m.get("model")
            if not tag:
                continue
            models.append(
                {
                    "model_id": tag,
                    "provider": "Local (Ollama)",
                    "name": tag,
                }
            )
        return models or static_models
    except Exception as e:  # pragma: no cover — exercised in tests via mock
        logger.info("ollama: list_available_models fallback (%s)", e)
        return static_models


def compare_scenarios(
    scenarios: List[Dict[str, Any]],
    question: Optional[str] = None,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> dict:
    """Compare RF scenarios via local Llama-3 (mirrors Bedrock signature)."""
    context = json.dumps({"scenarios": scenarios})
    default_question = (
        "Compare these RF scenarios in detail. For each scenario, assess "
        "signal quality, Fresnel zone clearance, and overall feasibility. "
        "Recommend which scenario is best and explain the engineering "
        "trade-offs. Consider both performance and practical deployment "
        "factors."
    )
    return invoke_model(
        prompt=question or default_question,
        model_id=model_id,
        max_tokens=max_tokens or 2048,
        temperature=temperature,
        context=context,
    )


def analyze_batch(
    batch_results: List[Dict[str, Any]],
    question: Optional[str] = None,
    model_id: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> dict:
    """Batch-analysis summary via local Llama-3 (mirrors Bedrock signature)."""
    context = json.dumps({"batch_results": batch_results})
    default_question = (
        "Analyze this batch of RF link results comprehensively. Provide:\n"
        "1. Overall coverage assessment (% feasible, signal distribution)\n"
        "2. Identification of the worst-performing links and why they fail\n"
        "3. Prioritized remediation recommendations (raise antenna, add "
        "repeater, change frequency)\n"
        "4. Estimated improvement if the top recommendation is implemented\n"
        "5. Summary table of coverage quality distribution"
    )
    return invoke_model(
        prompt=question or default_question,
        model_id=model_id,
        max_tokens=max_tokens or 2048,
        temperature=temperature,
        context=context,
    )


def suggest_antenna_height(
    analysis: Dict[str, Any],
    tower: Dict[str, Any],
    target_clearance: float = 0.6,
    model_id: Optional[str] = None,
) -> dict:
    """Antenna-height advisor via local Llama-3 (mirrors Bedrock signature)."""
    context = json.dumps(
        {
            "analysis": analysis,
            "tower": tower,
            "target_fresnel_clearance": target_clearance,
        }
    )
    prompt = (
        f"Based on the link analysis data and terrain profile provided, "
        f"calculate what antenna height is required to achieve at least "
        f"{target_clearance*100:.0f}% first Fresnel zone clearance. Consider:\n"
        f"1. Current antenna height and Fresnel clearance\n"
        f"2. The terrain profile and where the worst obstruction is\n"
        f"3. Earth curvature at this distance\n"
        f"4. Whether raising the TX antenna, RX antenna, or both is more "
        f"effective\n"
        f"5. Practical height limits for the tower structure\n"
        f"Provide a specific height recommendation with engineering "
        f"justification."
    )
    return invoke_model(
        prompt=prompt,
        model_id=model_id,
        max_tokens=2048,
        context=context,
    )


def health_check() -> Dict[str, Any]:
    """Lightweight liveness probe for the Ollama daemon.

    Used by ``/health/llm`` (when wired) and by tests. Returns a dict
    with ``ok`` (bool), ``base_url`` and either ``models`` or ``error``.
    """
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        resp.raise_for_status()
        body = resp.json()
        return {
            "ok": True,
            "base_url": OLLAMA_BASE_URL,
            "models": [m.get("name") for m in body.get("models", []) if m.get("name")],
        }
    except Exception as e:
        return {"ok": False, "base_url": OLLAMA_BASE_URL, "error": str(e)}
