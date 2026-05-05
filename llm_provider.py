# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
llm_provider — single import surface for the AI Playground.

Selects the LLM backend at import time based on ``LLM_PROVIDER``:

* ``LLM_PROVIDER=bedrock`` (default) → ``bedrock_service`` (Amazon Bedrock).
* ``LLM_PROVIDER=ollama``            → ``rf_engines.llm_engine`` (local Llama-3 via Ollama).

Both backends expose the same public functions with identical
signatures: ``invoke_model``, ``list_available_models``,
``compare_scenarios``, ``analyze_batch``, ``suggest_antenna_height``.
This module re-exports whichever set is active so the API handlers
can do ``from llm_provider import invoke_model`` without caring about
the underlying transport.

The provider name is also exposed as :data:`PROVIDER` for /health
endpoints and structured logging.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

PROVIDER = os.getenv("LLM_PROVIDER", "bedrock").strip().lower()

if PROVIDER == "ollama":
    logger.info("llm_provider: routing AI Playground calls to local Ollama backend")
    from rf_engines.llm_engine import (  # noqa: F401
        analyze_batch,
        compare_scenarios,
        invoke_model,
        list_available_models,
        suggest_antenna_height,
    )
elif PROVIDER in ("bedrock", ""):
    logger.info("llm_provider: routing AI Playground calls to Amazon Bedrock backend")
    PROVIDER = "bedrock"
    from bedrock_service import (  # noqa: F401
        analyze_batch,
        compare_scenarios,
        invoke_model,
        list_available_models,
        suggest_antenna_height,
    )
else:
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={PROVIDER!r}. Expected 'bedrock' or 'ollama'."
    )

__all__ = [
    "PROVIDER",
    "invoke_model",
    "list_available_models",
    "compare_scenarios",
    "analyze_batch",
    "suggest_antenna_height",
]
