"""Phase 6 tests: health check factory + service integration."""
from __future__ import annotations

import sys
from pathlib import Path
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common_sdk.health import build_health_router


@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    yield


def test_build_health_router_healthz_always_ok():
    """healthz returns 200 with service name regardless of dependencies."""
    app = FastAPI()
    app.include_router(build_health_router("test-svc", check_ready=None))
    client = TestClient(app)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "test-svc"


def test_build_health_router_readyz_200_when_all_ok():
    """readyz returns 200 when all dependencies report True."""
    async def check_ready():
        return {"redis": True, "mysql": True}

    app = FastAPI()
    app.include_router(build_health_router("test-svc", check_ready=check_ready))
    client = TestClient(app)

    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"redis": "ok", "mysql": "ok"}


def test_build_health_router_readyz_503_when_dependency_fail():
    """readyz returns 503 when any dependency reports False."""
    async def check_ready():
        return {"redis": False, "mysql": True}

    app = FastAPI()
    app.include_router(build_health_router("test-svc", check_ready=check_ready))
    client = TestClient(app)

    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"redis": "fail", "mysql": "ok"}


def test_build_health_router_readyz_no_check_returns_ready():
    """readyz with check_ready=None always returns ready (for stateless gateways)."""
    app = FastAPI()
    app.include_router(build_health_router("mcp-gateway", check_ready=None))
    client = TestClient(app)

    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_crawl_scheduler_main_exposes_health_and_metrics():
    """crawl_scheduler main uses the factory + setup_metrics, exposing /healthz and /metrics."""
    from project.backend.crawl_scheduler.main import app

    # Bypass lifespan (avoids real Redis/MySQL connection in test env)
    app.router.lifespan_context = _noop_lifespan
    client = TestClient(app)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "crawl-scheduler"

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


def test_pipeline_orchestrator_main_exposes_metrics():
    """pipeline_orchestrator (previously missing /metrics) now has it via setup_metrics."""
    from project.backend.pipeline_orchestrator.main import app

    # Bypass lifespan (avoids real MySQL connection + subscriber start in test env)
    app.router.lifespan_context = _noop_lifespan
    client = TestClient(app)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["service"] == "pipeline-orchestrator"

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
