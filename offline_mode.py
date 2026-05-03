# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Centralised offline-mode flag.

When ``TTP_OFFLINE=1`` (or ``true`` / ``yes``) the platform refuses to
make outbound calls to commercial third parties:

* **Stripe** — ``create_checkout_session`` and ``handle_webhook_event``
  raise :class:`OfflineModeError` (HTTP 503 at the API boundary).
* **Bedrock** — ``invoke_model`` and ``list_available_models`` short-
  circuit with a fixed canned response so the AI Playground UI degrades
  gracefully instead of stack-tracing.
* **Planet Labs API** — the satellite-change robot (run via cron, not
  in the request path) skips the HTTP call and emits an empty report.

What stays online
-----------------
Local-only services keep working: SRTM tile cache, MapBiomas raster,
RF engines (P.1812, ITM-logic, Sionna ML when its ``.tflite`` is on
disk), Postgres / Redis, RadioPlanner / Atoll / Planet exporters.

Why a single module
-------------------
Each consumer of this flag should call :func:`is_offline` (cheap,
re-reads the env var on every call so tests can flip it) or
:func:`require_online` (raises). Centralising the flag avoids drift
between modules — the admins of an air-gapped install only have to
know one variable name.
"""
from __future__ import annotations

import os

__all__ = ["OfflineModeError", "is_offline", "require_online"]


class OfflineModeError(RuntimeError):
    """Raised when an online-only feature is invoked while offline.

    Carries the feature name so the API layer can surface it in the
    HTTP error body.
    """

    def __init__(self, feature: str, *, hint: str = "") -> None:
        message = f"feature '{feature}' is unavailable in TTP_OFFLINE mode"
        if hint:
            message = f"{message}: {hint}"
        super().__init__(message)
        self.feature = feature
        self.hint = hint


def is_offline() -> bool:
    """Return ``True`` when ``TTP_OFFLINE`` is set to a truthy value.

    Reads the environment variable on every call (no caching) so tests
    that monkey-patch ``os.environ`` don't have to reload the module.
    """
    return os.getenv("TTP_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def require_online(feature: str, *, hint: str = "") -> None:
    """Raise :class:`OfflineModeError` if ``TTP_OFFLINE`` is set.

    Call at the top of any function that performs an outbound HTTPS
    request to a paid third party. The ``feature`` string ends up in
    log lines and HTTP error responses.
    """
    if is_offline():
        raise OfflineModeError(feature, hint=hint)
