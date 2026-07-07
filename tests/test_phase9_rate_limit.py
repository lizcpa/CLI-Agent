"""Phase 9 Part 1: RateLimiter middleware tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from common_sdk.middleware import RateLimitMiddleware
from common_sdk.resilience import RateLimiter


class TestRateLimiterTryAcquire:
    @pytest.mark.asyncio
    async def test_try_acquire_succeeds_within_burst(self):
        limiter = RateLimiter(rate=1.0, burst=3)
        assert await limiter.try_acquire() is True
        assert await limiter.try_acquire() is True
        assert await limiter.try_acquire() is True

    @pytest.mark.asyncio
    async def test_try_acquire_fails_after_burst_exhausted(self):
        limiter = RateLimiter(rate=0.1, burst=2)
        assert await limiter.try_acquire() is True
        assert await limiter.try_acquire() is True
        assert await limiter.try_acquire() is False


class TestRateLimitMiddleware:
    def _make_app(self, rate_paths=None):
        app = FastAPI()

        @app.get("/api/v1/pipelines")
        async def pipelines():
            return {"ok": True}

        @app.get("/api/v1/compose")
        async def compose():
            return {"ok": True}

        @app.get("/healthz")
        async def healthz():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, rate_paths=rate_paths)
        return app

    def test_rate_limit_returns_429_after_burst(self):
        app = self._make_app(rate_paths={"/api/v1/pipelines": (0.01, 2)})
        client = TestClient(app)

        r1 = client.get("/api/v1/pipelines")
        r2 = client.get("/api/v1/pipelines")
        r3 = client.get("/api/v1/pipelines")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429

    def test_rate_limit_independent_per_path(self):
        app = self._make_app(rate_paths={
            "/api/v1/pipelines": (0.1, 1),
            "/api/v1/compose": (0.1, 1),
        })
        client = TestClient(app)

        assert client.get("/api/v1/pipelines").status_code == 200
        assert client.get("/api/v1/pipelines").status_code == 429
        assert client.get("/api/v1/compose").status_code == 200

    def test_unlimited_paths_not_affected(self):
        app = self._make_app(rate_paths={"/api/v1/pipelines": (0.1, 1)})
        client = TestClient(app)

        for _ in range(20):
            assert client.get("/healthz").status_code == 200
