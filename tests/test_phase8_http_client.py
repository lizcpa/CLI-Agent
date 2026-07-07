"""Phase 8 tests: InternalHTTPClient response handling + per-target breaker isolation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import httpx
import pytest

from common_sdk.http_client import InternalHTTPClient
from common_sdk.exceptions import AppException, ServiceException


def _make_client(transport: httpx.MockTransport, **kwargs) -> InternalHTTPClient:
    """Build an InternalHTTPClient with a pre-injected mock transport.

    Bypasses lazy init by setting _client directly, so no real network is used.
    """
    client = InternalHTTPClient("test-svc", timeout=5.0, max_retries=1, **kwargs)
    client._client = httpx.AsyncClient(transport=transport)
    return client


async def test_handle_response_5xx_raises_service_exception():
    def handler(req):
        return httpx.Response(500, text="Internal Server Error")

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ServiceException):
            await client.post("http://test/api", target="t1")
    finally:
        await client.close()


async def test_handle_response_4xx_raises_app_exception():
    def handler(req):
        return httpx.Response(404, json={"code": 404, "message": "Not Found"})

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(AppException) as exc_info:
            await client.get("http://test/api", target="t1")
        assert exc_info.value.http_status == 404
    finally:
        await client.close()


async def test_handle_response_429_raises_service_exception():
    def handler(req):
        return httpx.Response(429, text="Too Many Requests")

    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ServiceException):
            await client.post("http://test/api", target="t1")
    finally:
        await client.close()


async def test_per_target_breaker_isolation():
    """Target A failing does not block target B (independent breakers)."""

    def handler(req):
        if "fail" in str(req.url):
            return httpx.Response(500, text="err")
        return httpx.Response(200, json={"ok": True})

    client = _make_client(
        httpx.MockTransport(handler),
        cb_failure_threshold=1,
    )
    try:
        with pytest.raises(ServiceException):
            await client.post("http://test/fail", target="A")
        result = await client.get("http://test/ok", target="B")
        assert result == {"ok": True}
    finally:
        await client.close()


async def test_close_releases_client():
    client = InternalHTTPClient("test-svc")
    client._client = httpx.AsyncClient()
    await client.close()
    assert client._client is None
