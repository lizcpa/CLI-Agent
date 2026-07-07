from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from .models import CrawlRequest, CrawlResult, PlatformAdapterConfig, StandardProduct

logger = logging.getLogger(__name__)


class BasePlatformCrawler(ABC):
    platform_id: str
    platform_config: PlatformAdapterConfig

    def __init__(self, platform_config: PlatformAdapterConfig) -> None:
        self.platform_id = platform_config.platform_id
        self.platform_config = platform_config
        self._redis: Any = None

    @abstractmethod
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        ...

    async def run_crawl(self, request: CrawlRequest) -> CrawlResult:
        start = time.perf_counter_ns()
        await self.rate_limit_wait()
        try:
            result = await self.crawl(request)
            result.products = self.validate_response(result.products)
        except Exception:
            logger.exception("Crawl failed for platform=%s keyword=%s", self.platform_id, request.keyword)
            result = CrawlResult(products=[], total_found=0)
        result.crawl_duration_ms = int((time.perf_counter_ns() - start) / 1_000_000)
        return result

    def validate_response(self, products: list[StandardProduct]) -> list[StandardProduct]:
        validated: list[StandardProduct] = []
        for p in products:
            if not p.title or not p.platform_product_id:
                continue
            if p.price is not None and p.price < 0:
                p.price = None
            if p.rating < 0:
                p.rating = 0.0
            if p.rating > 5:
                p.rating = 5.0
            validated.append(p)
        return validated

    async def rate_limit_wait(self) -> None:
        limit_str = self.platform_config.rate_limit
        try:
            count_str, period = limit_str.split("/")
            count = int(count_str)
        except (ValueError, AttributeError):
            return
        period_seconds = {"min": 60, "sec": 1}.get(period, 60)
        delay = period_seconds / max(count, 1)
        await asyncio.sleep(delay)

    async def acquire_crawl_lock(self, platform: str, keyword: str) -> bool:
        if self._redis is None:
            return True
        lock_key = f"crawl_lock:{platform}:{hashlib.md5(keyword.encode()).hexdigest()}"
        acquired = await self._redis.set(lock_key, "1", nx=True, ex=300)
        return bool(acquired)

    async def release_crawl_lock(self) -> None:
        pass


class APIDirectCrawler(BasePlatformCrawler):
    api_client: Any

    def __init__(self, platform_config: PlatformAdapterConfig) -> None:
        super().__init__(platform_config)
        self.api_client = None

    def sign_request(self, params: dict[str, Any]) -> dict[str, Any]:
        return params


class RenderCrawler(BasePlatformCrawler):
    browser: Any
    proxy_pool: Any

    def __init__(self, platform_config: PlatformAdapterConfig) -> None:
        super().__init__(platform_config)
        self.browser = None
        self.proxy_pool = None

    async def render_page(self, url: str) -> str:
        raise NotImplementedError
