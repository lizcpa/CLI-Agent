"""Phase 7 tests: OpenTelemetry tracing setup (common_sdk.tracing).

Verifies fail-soft behavior, app instrumentation, and idempotency.
Uses monkeypatch to control OTEL_ENABLED env var and stubs the OTLP
exporter + BatchSpanProcessor to avoid real gRPC connection / background
threads during the test.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI

import common_sdk.tracing as tracing_module
from common_sdk.tracing import setup_tracing


class _FakeExporter:
    """Stub OTLP exporter — no gRPC channel, no network."""
    def __init__(self, *args, **kwargs):
        self.endpoint = kwargs.get("endpoint")

    def export(self, *args, **kwargs):
        return None

    def shutdown(self):
        return None


class _FakeSpanProcessor:
    """Stub processor — captures the exporter, no background thread."""
    def __init__(self, exporter, *args, **kwargs):
        self.exporter = exporter

    def on_start(self, *args, **kwargs):
        return None

    def on_end(self, *args, **kwargs):
        return None

    def shutdown(self):
        return None

    def force_flush(self, *args, **kwargs):
        return True


def _install_fakes(monkeypatch):
    """Replace OTLP exporter + BatchSpanProcessor + set_tracer_provider in tracing module."""
    monkeypatch.setattr(tracing_module, "OTLPSpanExporter", _FakeExporter)
    monkeypatch.setattr(tracing_module, "BatchSpanProcessor", _FakeSpanProcessor)

    set_provider_calls: list = []

    def _capture_set_provider(provider):
        set_provider_calls.append(provider)

    monkeypatch.setattr(
        "opentelemetry.trace.set_tracer_provider", _capture_set_provider
    )
    return set_provider_calls


def test_setup_tracing_noop_when_otel_disabled(monkeypatch):
    """setup_tracing returns immediately without instrumenting when OTEL_ENABLED != true."""
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    monkeypatch.setattr(tracing_module, "_instrumented", False)
    set_provider_calls = _install_fakes(monkeypatch)

    app = FastAPI()
    setup_tracing(app, "test-svc")  # must not raise

    assert tracing_module._instrumented is False
    assert set_provider_calls == []  # provider never set


def test_setup_tracing_instruments_app_when_enabled(monkeypatch):
    """setup_tracing sets TracerProvider + instruments FastAPI when OTEL_ENABLED=true."""
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://fake-jaeger:4317")
    monkeypatch.setattr(tracing_module, "_instrumented", False)
    set_provider_calls = _install_fakes(monkeypatch)

    app = FastAPI()
    setup_tracing(app, "test-svc")

    # TracerProvider was set exactly once with a real TracerProvider instance
    assert len(set_provider_calls) == 1
    assert set_provider_calls[0].__class__.__name__ == "TracerProvider"

    # Resource service.name matches the argument
    resource = set_provider_calls[0].resource
    assert resource.attributes.get("service.name") == "test-svc"

    # _instrumented flag flipped (global httpx/redis instrumentation done)
    assert tracing_module._instrumented is True


def test_setup_tracing_idempotent_does_not_reset_provider(monkeypatch):
    """Second call re-instruments app but does not re-set TracerProvider."""
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://fake-jaeger:4317")
    monkeypatch.setattr(tracing_module, "_instrumented", False)
    set_provider_calls = _install_fakes(monkeypatch)

    app1 = FastAPI()
    setup_tracing(app1, "svc-1")
    assert len(set_provider_calls) == 1

    # Second call: _instrumented already True → only re-instruments app, no new provider
    app2 = FastAPI()
    setup_tracing(app2, "svc-2")  # must not raise

    assert len(set_provider_calls) == 1  # provider set only once
    assert tracing_module._instrumented is True
