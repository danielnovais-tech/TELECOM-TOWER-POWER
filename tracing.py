"""
tracing.py
Distributed tracing bootstrap for the TELECOM TOWER POWER API.

Evaluation mode: tracing is OFF by default. Enable by setting:
    OTEL_ENABLED=true
    OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317   # OTLP/gRPC into Jaeger
    OTEL_SERVICE_NAME=telecom-tower-power-api         # optional
    OTEL_TRACES_SAMPLER_ARG=0.05                      # head sample ratio
                                                      # (default 5%)

If `OTEL_ENABLED` is not truthy, `setup_tracing()` is a no-op — so the
production path keeps zero overhead until we opt in.

The instrumentation packages (opentelemetry-instrumentation-fastapi etc.)
are imported lazily inside setup_tracing(), so the API still starts cleanly
when tracing is disabled even if the OTel wheels aren't installed.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("telecom_tower_power.tracing")


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def tracing_enabled() -> bool:
    return _truthy(os.getenv("OTEL_ENABLED"))


def setup_tracing(app, service_name: Optional[str] = None) -> bool:
    """Configure OpenTelemetry tracing for the FastAPI app.

    Returns True if tracing was installed, False otherwise.

    Safe to call multiple times; subsequent calls are no-ops.
    """
    if not tracing_enabled():
        logger.info("tracing disabled (set OTEL_ENABLED=true to enable)")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
    except ImportError:
        logger.warning(
            "OTEL_ENABLED=true but opentelemetry packages are not installed; "
            "skipping tracing. Install with: pip install -r requirements.txt"
        )
        return False

    name = service_name or os.getenv("OTEL_SERVICE_NAME", "telecom-tower-power-api")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    # OTLP/gRPC is cleartext inside the private docker network.
    insecure = _truthy(os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true"))

    # Avoid double-install if called twice.
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider) and getattr(existing, "_ttp_configured", False):
        logger.info("tracing already configured; skipping")
        return True

    resource = Resource.create({"service.name": name})
    # Head-based sampling: keep ~5% of root traces by default. Honour the
    # parent decision when one is propagated so distributed traces stay
    # consistent across services. Override with OTEL_TRACES_SAMPLER_ARG
    # (a float in [0.0, 1.0]).
    try:
        ratio = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "0.05"))
    except ValueError:
        ratio = 0.05
    ratio = max(0.0, min(1.0, ratio))
    sampler = ParentBased(root=TraceIdRatioBased(ratio))
    provider = TracerProvider(resource=resource, sampler=sampler)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    provider._ttp_configured = True  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI request handlers and outbound `requests` calls.
    # Exclude the /metrics endpoint so Prometheus scrapes don't pollute traces.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health")
    RequestsInstrumentor().instrument()

    logger.info(
        "tracing enabled (service=%s, endpoint=%s, sample_ratio=%.3f)",
        name, endpoint, ratio,
    )
    return True
