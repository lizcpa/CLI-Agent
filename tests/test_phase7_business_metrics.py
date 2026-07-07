"""Phase 7 tests: custom business Prometheus metrics (Counters/Histograms/Gauge).

Verifies that metrics registered in common_sdk.business_metrics and
mcp_gateway.routes.mcp_active_sessions are exposed on /metrics and
behave correctly under inc/observe.
"""
from __future__ import annotations

import sys
from pathlib import Path
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from common_sdk.metrics import setup_metrics
from common_sdk.business_metrics import (
    ai_generation_duration_seconds,
    ai_generation_requests_total,
    crawl_jobs_total,
    crawl_products_found,
    pipeline_runs_total,
    publish_jobs_total,
    video_compose_jobs_total,
)


@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    yield


def _metric_names() -> set[str]:
    return {m.name for m in REGISTRY.collect()}


def test_business_metrics_registered_with_correct_types():
    """All 7 business metrics are registered in the default registry.

    Note: prometheus_client strips a trailing ``_total`` from Counter names
    when storing in the registry (so ``Counter("crawl_jobs_total")`` is
    stored under ``crawl_jobs``), but the sample emitted on /metrics is
    ``crawl_jobs_total``. We therefore verify via generate_latest output
    plus instance type checks on the imported objects.
    """
    # Type checks on imported objects (proves module imported + correct class)
    assert isinstance(crawl_jobs_total, Counter)
    assert isinstance(crawl_products_found, Histogram)
    assert isinstance(ai_generation_requests_total, Counter)
    assert isinstance(ai_generation_duration_seconds, Histogram)
    assert isinstance(video_compose_jobs_total, Counter)
    assert isinstance(publish_jobs_total, Counter)
    assert isinstance(pipeline_runs_total, Counter)

    # All 7 metric families exposed in the Prometheus text output
    output = generate_latest(REGISTRY).decode("utf-8")
    expected_samples = [
        "crawl_jobs_total",
        "crawl_products_found",
        "ai_generation_requests_total",
        "ai_generation_duration_seconds",
        "video_compose_jobs_total",
        "publish_jobs_total",
        "pipeline_runs_total",
    ]
    for sample in expected_samples:
        assert sample in output, f"metric {sample} not exposed on /metrics"


def test_counter_inc_reflected_in_metrics_output():
    """Incrementing a labeled Counter appears in generate_latest output."""
    crawl_jobs_total.labels(platform="taobao", status="success").inc()
    crawl_jobs_total.labels(platform="taobao", status="failed").inc(2)

    output = generate_latest(REGISTRY).decode("utf-8")
    assert 'crawl_jobs_total{platform="taobao",status="success"} 1.0' in output
    assert 'crawl_jobs_total{platform="taobao",status="failed"} 2.0' in output


def test_histogram_observe_reflected_in_metrics_output():
    """Observing a Histogram updates its _sum and _count series."""
    ai_generation_duration_seconds.labels(adapter_type="llm").observe(1.5)
    ai_generation_duration_seconds.labels(adapter_type="llm").observe(3.0)

    output = generate_latest(REGISTRY).decode("utf-8")
    # _count and _sum are exposed as separate series
    assert 'ai_generation_duration_seconds_count{adapter_type="llm"} 2.0' in output
    assert 'ai_generation_duration_seconds_sum{adapter_type="llm"} 4.5' in output


def test_mcp_active_sessions_gauge_tracks_inc_dec():
    """mcp_gateway's mcp_active_sessions Gauge reflects inc/dec on /metrics."""
    from project.backend.mcp_gateway.routes import mcp_active_sessions

    assert isinstance(mcp_active_sessions, Gauge)

    # Reset to known baseline then exercise inc/dec
    mcp_active_sessions.set(0)
    mcp_active_sessions.inc()
    mcp_active_sessions.inc()
    mcp_active_sessions.dec()

    output = generate_latest(REGISTRY).decode("utf-8")
    assert "mcp_active_sessions 1.0" in output
