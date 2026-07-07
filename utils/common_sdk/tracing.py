"""OpenTelemetry tracing setup.

Auto-instruments FastAPI + httpx + redis and exports spans via OTLP to
Jaeger (OTEL_EXPORTER_OTLP_ENDPOINT env var). Disabled when
OTEL_ENABLED != "true" (fail-soft — dev environments without Jaeger).

Note: opentelemetry-instrumentation-aiomysql is not available on PyPI,
so MySQL queries are not auto-traced; httpx + FastAPI + redis spans
cover the majority of cross-service call chains.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_instrumented = False


def setup_tracing(app: FastAPI, service_name: str) -> None:
    """Instrument `app` and export spans to Jaeger via OTLP.

    No-op when OTEL_ENABLED != "true". Safe to call from every service's
    main.py — global instrumentation (httpx/redis) runs only once.
    """
    global _instrumented
    if os.getenv("OTEL_ENABLED", "false").lower() != "true":
        return
    if _instrumented:
        FastAPIInstrumentor.instrument_app(app)
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    _instrumented = True
