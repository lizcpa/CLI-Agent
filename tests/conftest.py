"""Shared pytest configuration.

Injects `utils/` and project root onto sys.path so individual test files
do not need to repeat the boilerplate. Existing test files that already
do their own `sys.path.insert` remain compatible (idempotent).
"""
from __future__ import annotations

import sys
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_UTILS_DIR = _PROJECT_ROOT / "utils"

for _p in (_UTILS_DIR, _PROJECT_ROOT):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

import pytest
import fakeredis.aioredis

from common_sdk.auth import create_service_jwt


SECRET = "dev-jwt-secret-prodvideofactory-2024"


class _StubState:
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id
        self.service_name = "test-service"


class StubRequest:
    """Minimal FastAPI Request stub for E2E tests."""
    def __init__(self, tenant_id: str = "default", headers: dict | None = None) -> None:
        self.state = _StubState(tenant_id)
        self.headers = headers or {}


class MockHTTPClient:
    """Mock InternalHTTPClient that records all calls and returns predefined responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: dict[str, Any] = {}
        self._default_response: dict[str, Any] = {"code": 0, "data": {}}

    def set_response(self, path_pattern: str, response: dict[str, Any]) -> None:
        """Set a response for calls matching path_pattern (substring match)."""
        self._responses[path_pattern] = response

    def set_default_response(self, response: dict[str, Any]) -> None:
        """Set the default response when no pattern matches."""
        self._default_response = response

    async def post(self, url: str, json_data: dict | None = None, target: str = "", tenant_id: str = "", **kwargs) -> dict:
        call = {"method": "POST", "url": url, "json_data": json_data, "target": target, "tenant_id": tenant_id}
        self.calls.append(call)
        for pattern, resp in self._responses.items():
            if pattern in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return self._default_response

    async def get(self, url: str, target: str = "", tenant_id: str = "", **kwargs) -> dict:
        call = {"method": "GET", "url": url, "target": target, "tenant_id": tenant_id}
        self.calls.append(call)
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        return self._default_response

    async def close(self) -> None:
        pass

    def get_calls_matching(self, pattern: str) -> list[dict]:
        """Return all calls where url contains pattern."""
        return [c for c in self.calls if pattern in c["url"]]

    def get_tenant_id_for_call(self, pattern: str) -> str | None:
        """Return tenant_id header for first call matching pattern."""
        for c in self.calls:
            if pattern in c["url"]:
                return c.get("tenant_id")
        return None


class MockCeleryTask:
    """Mock Celery BaseTask with Redis client."""
    def __init__(self, redis_client):
        self.redis_client = redis_client


def pytest_configure(config):
    config._phase_start = None  # marker for future hooks; kept minimal


def pytest_unconfigure(config):
    pass


# ============= E2E Fixtures =============

@pytest.fixture
def mock_http_client():
    """Yield a MockHTTPClient and patch InternalHTTPClient."""
    client = MockHTTPClient()
    with patch("common_sdk.http_client.InternalHTTPClient", return_value=client):
        yield client


@pytest.fixture
async def mock_redis_client():
    """Yield a fakeredis async Redis client."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    try:
        await client.aclose()
    except Exception:
        pass


@pytest.fixture
def mock_jwt_token():
    """Generate a valid JWT for internal service authentication."""
    token = create_service_jwt("test-service", SECRET)
    return f"Bearer {token}"


@pytest.fixture
def mock_mcp_api_key():
    """Generate a test MCP API key (raw string)."""
    secret = "testsecret12345678"
    tenant_id = "test-tenant"
    raw_key = f"mcp_sk.{tenant_id}.{secret}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return {
        "raw_key": raw_key,
        "tenant_id": tenant_id,
        "key_hash": key_hash,
    }


@pytest.fixture
def stub_request():
    """Yield a StubRequest with default tenant_id."""
    return StubRequest()


@pytest.fixture
def stub_request_tenant_a():
    """Yield a StubRequest with tenant_id='tenant-A'."""
    return StubRequest(tenant_id="tenant-A")


@pytest.fixture
def stub_request_tenant_b():
    """Yield a StubRequest with tenant_id='tenant-B'."""
    return StubRequest(tenant_id="tenant-B")


@pytest.fixture
async def mock_redis_for_pipeline():
    """fakeredis for pipeline task status tracking."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    try:
        await client.aclose()
    except Exception:
        pass


@pytest.fixture
def mock_http_for_pipeline():
    """MockHTTPClient configured for full pipeline flow."""
    client = MockHTTPClient()

    # product-analyzer /analyze
    client.set_response("/api/v1/analyze", {"code": 0, "data": {"task_id": "analyze_001", "status": "queued"}})

    # ai-generation /copywriting
    client.set_response("/api/v1/copywriting", {"code": 0, "data": {"text": "Amazing product for you!"}})

    # ai-generation /images/generate
    client.set_response("/api/v1/images/generate", {
        "code": 0,
        "data": {"image_objects": ["img1.png", "img2.png", "img3.png"]},
    })

    # ai-generation /videos/generate
    client.set_response("/api/v1/videos/generate", {
        "code": 0,
        "data": {"clip_objects": ["clip1.mp4", "clip2.mp4"]},
    })

    # video-composer /compose
    client.set_response("/api/v1/compose", {
        "code": 0,
        "data": {"output_url": "https://minio/bucket/final_video.mp4"},
    })

    # publish-dispatcher /publish
    client.set_response("/api/v1/publish", {
        "code": 0,
        "data": {"pipeline_id": 1, "platform_tasks": [{"platform": "youtube", "task_id": "pub_001"}]},
    })

    return client


@pytest.fixture
def mock_mysql_client_for_pipeline():
    """Mock MySQL client for pipeline tests with in-memory behavior."""
    mock = MagicMock()
    _pipeline_id = 1
    _pipelines: dict[int, dict] = {}
    _products: dict[int, dict] = {1: {
        "id": 1, "tenant_id": "default", "title": "Test Product",
        "description": "Test", "main_image_url": "https://example.com/test.jpg",
        "tags": [], "score": 85.0, "tier": "hot", "platform": "taobao",
    }}

    async def _execute(sql, params=None):
        sql_upper = sql.upper()
        if "INSERT INTO generation_pipelines" in sql_upper:
            nonlocal _pipeline_id
            pipeline_id = _pipeline_id
            _pipeline_id += 1
            _pipelines[pipeline_id] = {
                "id": pipeline_id,
                "tenant_id": params[0] if params else "default",
                "product_id": params[1] if params and len(params) > 1 else 1,
                "stage": "pending",
                "copywriting": None,
                "copywriting_status": "pending",
                "image_urls": None,
                "images_status": "pending",
                "video_clip_urls": None,
                "video_clips_status": "pending",
                "final_video_url": None,
                "compose_status": "pending",
                "publish_status": "pending",
            }
        elif "UPDATE generation_pipelines" in sql_upper:
            if params:
                pipeline_id = params[-1]
                if pipeline_id in _pipelines:
                    for i, field in enumerate(["stage", "copywriting", "copywriting_status", 
                                               "image_urls", "images_status", "video_clip_urls",
                                               "video_clips_status", "final_video_url", 
                                               "compose_status", "publish_status", "error_message"]):
                        if i < len(params):
                            _pipelines[pipeline_id][field] = params[i]

    async def _fetchone(sql, params=None):
        sql_upper = sql.upper()
        if "LAST_INSERT_ID" in sql_upper:
            nonlocal _pipeline_id
            return {"id": _pipeline_id - 1}
        if "generation_pipelines" in sql_upper and "WHERE ID" in sql_upper:
            pid = params[0] if params else 1
            return _pipelines.get(pid)
        if "products" in sql_upper and "WHERE ID" in sql_upper:
            pid = params[0] if params else 1
            return _products.get(pid)
        return None

    mock.execute = AsyncMock(side_effect=_execute)
    mock.fetchone = AsyncMock(side_effect=_fetchone)
    mock.fetchall = AsyncMock(return_value=[])

    return mock, _pipelines


@pytest.fixture
async def mock_redis_for_resilience():
    """fakeredis for resilience tests."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    try:
        await client.aclose()
    except Exception:
        pass
