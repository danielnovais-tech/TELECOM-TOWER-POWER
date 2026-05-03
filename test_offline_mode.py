# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Tests for the centralised TTP_OFFLINE flag.

Each test flips ``TTP_OFFLINE`` via ``monkeypatch.setenv`` (the helper
re-reads on every call so no module reload dance is needed) and asserts
that the three integration points behave correctly:

1. ``offline_mode.is_offline`` / ``require_online`` semantics.
2. ``stripe_billing.create_checkout_session`` raises ``OfflineModeError``.
3. ``bedrock_service.invoke_model`` returns the canned response.

We do NOT test the satellite_change_robot here because it's a CLI
tool and has its own integration tests; we cover the offline branch
indirectly via ``test_satellite_change_robot.py`` separately.
"""
from __future__ import annotations

import importlib

import pytest

import offline_mode


# ---------------------------------------------------------------------------
# offline_mode.is_offline / require_online
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("1", True),
    ("true", True),
    ("True", True),
    ("YES", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
])
def test_is_offline_truthy(monkeypatch, value, expected):
    monkeypatch.setenv("TTP_OFFLINE", value)
    assert offline_mode.is_offline() is expected


def test_is_offline_unset(monkeypatch):
    monkeypatch.delenv("TTP_OFFLINE", raising=False)
    assert offline_mode.is_offline() is False


def test_require_online_passes_when_unset(monkeypatch):
    monkeypatch.delenv("TTP_OFFLINE", raising=False)
    # Should not raise.
    offline_mode.require_online("stripe.checkout")


def test_require_online_raises_when_set(monkeypatch):
    monkeypatch.setenv("TTP_OFFLINE", "1")
    with pytest.raises(offline_mode.OfflineModeError) as ei:
        offline_mode.require_online("stripe.checkout", hint="set the var")
    assert ei.value.feature == "stripe.checkout"
    assert "set the var" in str(ei.value)
    assert "TTP_OFFLINE" in str(ei.value)


# ---------------------------------------------------------------------------
# stripe_billing — gated entrypoints
# ---------------------------------------------------------------------------
def test_stripe_create_checkout_session_blocked_offline(monkeypatch):
    monkeypatch.setenv("TTP_OFFLINE", "1")
    sb = importlib.import_module("stripe_billing")
    with pytest.raises(offline_mode.OfflineModeError):
        sb.create_checkout_session("user@example.com", "pro")


def test_stripe_handle_webhook_blocked_offline(monkeypatch):
    monkeypatch.setenv("TTP_OFFLINE", "1")
    sb = importlib.import_module("stripe_billing")
    with pytest.raises(offline_mode.OfflineModeError):
        sb.handle_webhook_event(b"{}", "t=0,v1=deadbeef")


# ---------------------------------------------------------------------------
# bedrock_service — short-circuited entrypoints
# ---------------------------------------------------------------------------
def test_bedrock_invoke_model_canned_offline(monkeypatch):
    monkeypatch.setenv("TTP_OFFLINE", "1")
    br = importlib.import_module("bedrock_service")
    out = br.invoke_model("How does Fresnel zone work?")
    assert out["offline"] is True
    assert out["input_tokens"] == 0
    assert out["output_tokens"] == 0
    assert "offline mode" in out["response"].lower()


def test_bedrock_list_available_models_static_offline(monkeypatch):
    monkeypatch.setenv("TTP_OFFLINE", "1")
    br = importlib.import_module("bedrock_service")
    models = br.list_available_models()
    # Must return non-empty static list, never call out to AWS.
    assert isinstance(models, list)
    assert len(models) >= 1
    assert all("model_id" in m for m in models)
