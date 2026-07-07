from __future__ import annotations

import json
import logging
import os

import httpx

from platform_connectors.base_crawler import APIDirectCrawler
from platform_connectors.mapper import PlatformDataMapper
from platform_connectors.models import CrawlRequest, CrawlResult

logger = logging.getLogger(__name__)


class ShopeeCrawler(APIDirectCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        try:
            cfg = request.platform_config or self.platform_config.config or {}
            endpoint = cfg.get(
                "crawler_api_endpoint",
                "https://shopee.com/api/v4/search/search_items",
            )
            try:
                sort_map = json.loads(cfg.get("crawler_sort_map", "{}"))
            except (TypeError, json.JSONDecodeError):
                sort_map = {}
            sort_param = sort_map.get(request.sort_by, "relevancy")
            params = {
                "keyword": request.keyword,
                "limit": min(request.max_count, 50),
                "newest": 0,
                "by": sort_param,
                "order": "desc",
                "page_type": "search",
                "version": 2,
            }
            params = self.sign_request(params)
            headers = {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "accept": "application/json",
                "x-shopee-language": cfg.get("shopee_language", "en"),
                "x-api-source": "pc",
            }
            token = cfg.get("api_key", "") or os.environ.get("SHOPEE_API_KEY", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            afc_token = cfg.get("shopee_afc_token", "") or os.environ.get("SHOPEE_AFC_TOKEN", "")
            if afc_token:
                headers["x-afc-token"] = afc_token

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(endpoint, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            try:
                mapping = json.loads(cfg.get("crawler_mapping", "{}"))
            except (TypeError, json.JSONDecodeError):
                mapping = {}
            mapper = PlatformDataMapper({"shopee": mapping}) if mapping else PlatformDataMapper()
            items = (data.get("items") or [])[: request.max_count]
            products = [mapper.map_to_standard("shopee", item) for item in items]
            return CrawlResult(products=products, total_found=len(products), crawl_duration_ms=0)
        except Exception as e:
            logger.warning("shopee_crawl_failed", error=str(e))
            return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
