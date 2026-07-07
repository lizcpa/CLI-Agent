"""Phase 6 tests: Prometheus metrics setup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common_sdk.metrics import setup_metrics


def test_setup_metrics_exposes_endpoint():
    """setup_metrics registers /metrics returning Prometheus text format."""
    app = FastAPI()
    setup_metrics(app, "test-svc")

    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Prometheus exposition format is text/plain
    assert "text/plain" in resp.headers.get("content-type", "")
    # Default instrumentator exposes http_requests metrics after a request is made
    # The /metrics call itself may or may not be recorded; just check body is non-empty
    assert resp.text


def test_setup_metrics_records_request_traffic():
    """After hitting a non-excluded endpoint, /metrics contains http request metrics."""
    app = FastAPI()

    @app.get("/echo")
    async def echo():
        return {"ok": True}

    setup_metrics(app, "test-svc")
    client = TestClient(app)

    client.get("/echo")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # After traffic, http_requests_total or http_request_duration should appear
    body = resp.text
    assert "http_request" in body or "http_requests" in body


def test_setup_metrics_excludes_health_endpoints():
    """Calls to /healthz should not be counted in metrics (excluded_handlers)."""
    app = FastAPI()
    setup_metrics(app, "test-svc")

    client = TestClient(app)
    # Hit healthz multiple times
    for _ in range(3):
        client.get("/healthz")

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    # /healthz is in excluded_handlers, so no handler label should reference it
    assert 'handler="/healthz"' not in body
