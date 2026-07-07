"""Prometheus metrics helper.

Wraps `prometheus-fastapi-instrumentator` to auto-expose `/metrics` on every
service with consistent default HTTP latency/status/rate metrics. Health
endpoints are excluded so probe traffic does not skew metrics.
"""
from __future__ import annotations

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

_EXCLUDED_HANDLERS = ["/healthz", "/readyz", "/metrics", "/docs", "/openapi.json", "/redoc"]


def setup_metrics(app: FastAPI, service_name: str | None = None) -> None:
    """Instrument `app` and expose `/metrics` (Prometheus text format).

    The instrumentation is gated by env var `PROMETHEUS_METRICS_ENABLED`
    (default: enabled). When disabled, `/metrics` is still registered but
    reports no traffic.
    """
    Instrumentator(
        excluded_handlers=_EXCLUDED_HANDLERS,
        env_var_name="PROMETHEUS_METRICS_ENABLED",
    ).instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        tags=["monitoring"],
    )
