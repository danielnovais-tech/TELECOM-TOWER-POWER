# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
"""Tests for llm_provider routing via LLM_PROVIDER env var."""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _reload_provider(env_value: str | None):
    if env_value is None:
        os.environ.pop("LLM_PROVIDER", None)
    else:
        os.environ["LLM_PROVIDER"] = env_value
    sys.modules.pop("llm_provider", None)
    return importlib.import_module("llm_provider")


def test_default_provider_is_bedrock():
    mod = _reload_provider(None)
    assert mod.PROVIDER == "bedrock"
    # Bound to bedrock_service implementations.
    import bedrock_service
    assert mod.invoke_model is bedrock_service.invoke_model
    assert mod.list_available_models is bedrock_service.list_available_models


def test_ollama_provider_routes_to_local_engine():
    mod = _reload_provider("ollama")
    assert mod.PROVIDER == "ollama"
    from rf_engines import llm_engine
    assert mod.invoke_model is llm_engine.invoke_model
    assert mod.list_available_models is llm_engine.list_available_models
    assert mod.compare_scenarios is llm_engine.compare_scenarios
    assert mod.analyze_batch is llm_engine.analyze_batch
    assert mod.suggest_antenna_height is llm_engine.suggest_antenna_height


def test_unknown_provider_raises():
    os.environ["LLM_PROVIDER"] = "totally-not-a-backend"
    sys.modules.pop("llm_provider", None)
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        importlib.import_module("llm_provider")


def teardown_module(_module):
    # Restore default for other tests in the suite.
    os.environ.pop("LLM_PROVIDER", None)
    sys.modules.pop("llm_provider", None)
