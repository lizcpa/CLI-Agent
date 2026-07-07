from __future__ import annotations

import json
import logging
import os

import httpx

from platform_connectors.base_crawler import APIDirectCrawler
from platform_connectors.mapper import PlatformDataMapper
from platform_connectors.models import CrawlRequest, CrawlResult

logger = logging.getLogger(__name__)


class AmazonCrawler(APIDirectCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        try:
            cfg = request.platform_config or self.platform_config.config or {}
            endpoint = cfg.get(
                "crawler_api_endpoint",
                "https://webservices.amazon.com/paapi5/searchitems",
            )
            params = {
                "Keywords": request.keyword,
                "ItemCount": min(request.max_count, 10),
                "SearchIndex": cfg.get("crawler_search_index", "All"),
                "ItemPage": 1,
                "Resources": [
                    "ItemInfo.Title",
                    "Images.Primary.MediumURL",
                    "Offers.Listings.Price",
                    "CustomerReviews.StarRating",
                ],
            }
            params = self.sign_request(params)
            headers = {
                "x-amz-target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
                "content-type": "application/json; charset=utf-8",
            }
            token = cfg.get("api_key", "") or os.environ.get("AMAZON_API_KEY", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            partner_tag = cfg.get("amazon_partner_tag", "") or os.environ.get("AMAZON_PARTNER_TAG", "")
            if partner_tag:
                params["PartnerTag"] = partner_tag
            params["PartnerType"] = "Associates"
            params["Marketplace"] = cfg.get("amazon_marketplace", "www.amazon.com")

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(endpoint, json=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            try:
                mapping = json.loads(cfg.get("crawler_mapping", "{}"))
            except (TypeError, json.JSONDecodeError):
                mapping = {}
            mapper = PlatformDataMapper({"amazon": mapping}) if mapping else PlatformDataMapper()
            items = (data.get("SearchResult") or {}).get("Items", []) or []
            items = items[: request.max_count]
            products = [mapper.map_to_standard("amazon", item) for item in items]
            return CrawlResult(products=products, total_found=len(products), crawl_duration_ms=0)
        except Exception as e:
            logger.warning("amazon_crawl_failed", error=str(e))
            return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
