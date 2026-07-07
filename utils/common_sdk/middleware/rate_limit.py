"""ASGI middleware that applies per-path token-bucket rate limiting.

Importable as ``from common_sdk.middleware import RateLimitMiddleware``
(when ``utils/`` is on ``sys.path``) or
``from utils.common_sdk.middleware import RateLimitMiddleware``.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..resilience import RateLimiter

DEFAULT_RATE_PATHS: dict[str, tuple[float, int]] = {
    "/api/v1/pipelines": (5.0, 10),
    "/api/v1/compose": (5.0, 10),
    "/api/v1/publish": (5.0, 10),
    "/api/v1/crawl/jobs": (5.0, 10),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests exceeding the configured rate per path prefix.

    ``rate_paths`` maps a path prefix to ``(rate, burst)`` — tokens per
    second and maximum burst.  Each prefix gets an independent
    ``RateLimiter``.  Requests exceeding the rate receive HTTP 429.
    """

    def __init__(self, app, rate_paths: dict[str, tuple[float, int]] | None = None) -> None:
        super().__init__(app)
        paths = rate_paths if rate_paths is not None else DEFAULT_RATE_PATHS
        self._limiters: dict[str, RateLimiter] = {
            prefix: RateLimiter(rate=rate, burst=burst)
            for prefix, (rate, burst) in paths.items()
        }

    async def dispatch(self, request: Request, call_next):
        for prefix, limiter in self._limiters.items():
            if request.url.path.startswith(prefix):
                if not await limiter.try_acquire():
                    return JSONResponse(
                        status_code=429,
                        content={"code": 429, "message": "Rate limit exceeded"},
                    )
                break
        return await call_next(request)
