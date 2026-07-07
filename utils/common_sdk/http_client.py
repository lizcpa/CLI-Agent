"""Internal HTTP client with resilience patterns.

``InternalHTTPClient`` is the single entry point for service-to-service HTTP
calls.  It integrates per-target circuit breaker, bulkhead (concurrency cap),
and retry (exponential backoff + jitter) on top of a lazily-initialised
``httpx.AsyncClient`` (connection-pooled).

``InternalHTTPSyncClient`` is the legacy sync variant — retained unchanged for
backwards compatibility but deprecated; new code should use the async client.
"""
from __future__ import annotations

from typing import Any

import httpx

from .auth import create_service_jwt
from .config import config_manager
from .exceptions import AppException, ServiceException
from .resilience import Bulkhead, CircuitBreaker, retry


class InternalHTTPClient:
    """Async internal HTTP client with circuit breaker + retry + bulkhead.

    The ``httpx.AsyncClient`` is created lazily on first use so the instance
    can be constructed outside an event loop (e.g. at Celery task import time)
    and survive across ``asyncio.run()`` boundaries.  Remember to call
    ``close()`` when done to release the connection pool.
    """

    def __init__(
        self,
        service_name: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        cb_failure_threshold: int = 3,
        cb_cooldown_seconds: float = 30.0,
        bulkhead_concurrency: int = 10,
    ) -> None:
        self._service_name = service_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._cb_failure_threshold = cb_failure_threshold
        self._cb_cooldown_seconds = cb_cooldown_seconds
        self._bulkhead_concurrency = bulkhead_concurrency
        self._client: httpx.AsyncClient | None = None
        self._breakers: dict[str, CircuitBreaker] = {}
        self._bulkheads: dict[str, Bulkhead] = {}
        self._jwt_secret = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
        self._tenant_id = config_manager.get("TENANT_ID", "default")

    # ── httpx client lifecycle ─────────────────────────────────────

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── per-target breaker / bulkhead ───────────────────────────────

    def _get_breaker(self, target: str) -> CircuitBreaker:
        if target not in self._breakers:
            self._breakers[target] = CircuitBreaker(
                name=f"{self._service_name}->{target}",
                failure_threshold=self._cb_failure_threshold,
                cooldown_seconds=self._cb_cooldown_seconds,
            )
        return self._breakers[target]

    def _get_bulkhead(self, target: str) -> Bulkhead:
        if target not in self._bulkheads:
            self._bulkheads[target] = Bulkhead(max_concurrent=self._bulkhead_concurrency)
        return self._bulkheads[target]

    # ── headers ─────────────────────────────────────────────────────

    def _headers(self, tenant_id: str | None = None) -> dict[str, str]:
        token = create_service_jwt(self._service_name, self._jwt_secret)
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": tenant_id or self._tenant_id,
            "Content-Type": "application/json",
        }

    # ── HTTP methods ────────────────────────────────────────────────

    async def get(
        self,
        url: str,
        *,
        target: str = "default",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        breaker = self._get_breaker(target)
        bulkhead = self._get_bulkhead(target)

        async def _do() -> dict[str, Any]:
            resp = await self._ensure_client().get(url, headers=self._headers(tenant_id))
            return self._handle_response(resp)

        retried = retry(max_attempts=self._max_retries, name=target)(_do)
        async with bulkhead:
            return await breaker.call(retried)

    async def post(
        self,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
        target: str = "default",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        breaker = self._get_breaker(target)
        bulkhead = self._get_bulkhead(target)

        async def _do() -> dict[str, Any]:
            resp = await self._ensure_client().post(
                url, json=json_data, headers=self._headers(tenant_id)
            )
            return self._handle_response(resp)

        retried = retry(max_attempts=self._max_retries, name=target)(_do)
        async with bulkhead:
            return await breaker.call(retried)

    async def put(
        self,
        url: str,
        *,
        json_data: dict[str, Any] | None = None,
        target: str = "default",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        breaker = self._get_breaker(target)
        bulkhead = self._get_bulkhead(target)

        async def _do() -> dict[str, Any]:
            resp = await self._ensure_client().put(
                url, json=json_data, headers=self._headers(tenant_id)
            )
            return self._handle_response(resp)

        retried = retry(max_attempts=self._max_retries, name=target)(_do)
        async with bulkhead:
            return await breaker.call(retried)

    async def delete(
        self,
        url: str,
        *,
        target: str = "default",
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        breaker = self._get_breaker(target)
        bulkhead = self._get_bulkhead(target)

        async def _do() -> dict[str, Any]:
            resp = await self._ensure_client().delete(url, headers=self._headers(tenant_id))
            return self._handle_response(resp)

        retried = retry(max_attempts=self._max_retries, name=target)(_do)
        async with bulkhead:
            return await breaker.call(retried)

    # ── response handling ───────────────────────────────────────────

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            try:
                return response.json()
            except Exception:
                return {}
        status = response.status_code
        # 5xx or 429 → ServiceException (retryable)
        if status >= 500 or status == 429:
            raise ServiceException(
                message=f"Upstream {status}: {response.text[:200]}",
                data={"upstream_status": status},
            )
        # 4xx (non-429) → AppException (non-retryable); try to parse body
        try:
            body = response.json()
            raise AppException(
                code=body.get("code", status),
                message=body.get("message", "Upstream error"),
                http_status=status,
                data=body.get("data"),
            )
        except AppException:
            raise
        except Exception:
            raise AppException(
                code=status,
                message=f"Upstream {status}: {response.text[:200]}",
                http_status=status,
            )


class InternalHTTPSyncClient:
    """Deprecated sync variant. Retained for backwards compatibility."""

    def __init__(
        self,
        service_name: str,
        base_url: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._service_name = service_name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._jwt_secret = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
        self._tenant_id = config_manager.get("TENANT_ID", "default")

    def _headers(self) -> dict[str, str]:
        token = create_service_jwt(self._service_name, self._jwt_secret)
        return {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": self._tenant_id,
            "Content-Type": "application/json",
        }

    def get(self, url: str) -> dict[str, Any]:
        full_url = f"{self._base_url}{url}"
        response = httpx.get(full_url, headers=self._headers(), timeout=self._timeout)
        return self._handle_response(response)

    def post(self, url: str, json_data: dict[str, Any] | None = None) -> dict[str, Any]:
        full_url = f"{self._base_url}{url}"
        response = httpx.post(full_url, json=json_data, headers=self._headers(), timeout=self._timeout)
        return self._handle_response(response)

    def put(self, url: str, json_data: dict[str, Any] | None = None) -> dict[str, Any]:
        full_url = f"{self._base_url}{url}"
        response = httpx.put(full_url, json=json_data, headers=self._headers(), timeout=self._timeout)
        return self._handle_response(response)

    def delete(self, url: str) -> dict[str, Any]:
        full_url = f"{self._base_url}{url}"
        response = httpx.delete(full_url, headers=self._headers(), timeout=self._timeout)
        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            return response.json()
        try:
            body = response.json()
            raise AppException(
                code=body.get("code", response.status_code),
                message=body.get("message", "Upstream error"),
                http_status=response.status_code,
                data=body.get("data"),
            )
        except AppException:
            raise
        except Exception:
            raise ServiceException(
                message=f"Upstream HTTP {response.status_code}: {response.text[:200]}"
            )
