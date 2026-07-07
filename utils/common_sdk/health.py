"""Reusable health-check router factory.

Every backend service mounts the result of `build_health_router` to expose
consistent `/healthz` (liveness) and `/readyz` (readiness) endpoints:

- `/healthz` returns 200 with `{"status":"ok","service":...}` whenever the
  process is up. It performs no I/O so it can be polled aggressively.
- `/readyz` calls the service-supplied `check_ready` coroutine, which returns
  a dict of dependency-name -> bool. Returns 200 if all dependencies are
  reachable, otherwise 503 with per-dependency status.

`/metrics` is intentionally NOT registered here — it is provided by
`common_sdk.metrics.setup_metrics` via prometheus-fastapi-instrumentator.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

CheckReadyFn = Callable[[], Awaitable[dict[str, bool]]]


def build_health_router(
    service_name: str,
    check_ready: Optional[CheckReadyFn] = None,
) -> APIRouter:
    """Build an APIRouter containing `/healthz` and `/readyz`.

    Args:
        service_name: Logical service identifier (e.g. "crawl_scheduler").
        check_ready: Optional coroutine returning `{dep: bool}`. When None,
            `/readyz` always reports ready (used for stateless gateways).
    """
    router = APIRouter(tags=["health"])

    @router.get("/healthz", summary="Liveness probe")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    @router.get("/readyz", summary="Readiness probe")
    async def readyz() -> JSONResponse:
        if check_ready is None:
            return JSONResponse(
                status_code=200,
                content={"status": "ready", "service": service_name, "checks": {}},
            )
        try:
            checks = await check_ready()
        except Exception as exc:  # pragma: no cover - defensive
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": service_name,
                    "error": str(exc),
                    "checks": {},
                },
            )
        all_ok = bool(checks) and all(checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={
                "status": "ready" if all_ok else "not_ready",
                "service": service_name,
                "checks": {k: "ok" if v else "fail" for k, v in checks.items()},
            },
        )

    return router
