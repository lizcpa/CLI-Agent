from __future__ import annotations

import json
import logging
import urllib.parse

from platform_connectors.base_crawler import RenderCrawler
from platform_connectors.mapper import PlatformDataMapper
from platform_connectors.models import CrawlRequest, CrawlResult

logger = logging.getLogger(__name__)

DEFAULT_EXTRACT = """
() => {
    const items = [];
    const selectors = '.Card--doubleCardWrapper, .J_AjaxUserFeed, [class*="Content--contentInner"], [class*="card"]';
    document.querySelectorAll(selectors).forEach(el => {
        const titleEl = el.querySelector('[class*="title"], .title, a[title]');
        const imgEl = el.querySelector('img');
        const priceEl = el.querySelector('[class*="price"], .price, [class*="Price"]');
        const linkEl = el.querySelector('a[href]');
        const rawId = el.getAttribute('data-id') || (linkEl && linkEl.href) || (imgEl && imgEl.src) || '';
        const priceText = (priceEl && priceEl.textContent) || '';
        const priceMatch = priceText.match(/[0-9]+(?:\\.[0-9]+)?/);
        items.push({
            title: (titleEl && (titleEl.getAttribute('title') || titleEl.textContent)) || '',
            platform_product_id: rawId,
            main_image_url: (imgEl && imgEl.src) || '',
            price: priceMatch ? priceMatch[0] : '',
            sales_count: '',
            item_url: (linkEl && linkEl.href) || '',
        });
    });
    return items;
}
"""


class TaobaoCrawler(RenderCrawler):
    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        try:
            cfg = request.platform_config or self.platform_config.config or {}
            url_template = cfg.get(
                "crawler_url_template",
                "https://s.taobao.com/search?q={keyword}",
            )
            try:
                sort_map = json.loads(cfg.get("crawler_sort_map", "{}"))
            except (TypeError, json.JSONDecodeError):
                sort_map = {}
            sort_param = sort_map.get(request.sort_by, "")
            url = url_template.format(keyword=urllib.parse.quote(request.keyword))
            if sort_param:
                url += f"&sort={urllib.parse.quote(str(sort_param))}"

            from platform_connectors._playwright import render_page

            extract_script = cfg.get("crawler_extract_script", DEFAULT_EXTRACT)
            data = await render_page(
                url,
                wait_for_selector=cfg.get("crawler_wait_selector"),
                extract_script=extract_script,
            )
            if not data:
                return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)

            try:
                mapping = json.loads(cfg.get("crawler_mapping", "{}"))
            except (TypeError, json.JSONDecodeError):
                mapping = {}
            mapper = PlatformDataMapper({"taobao": mapping}) if mapping else PlatformDataMapper()
            items = data if isinstance(data, list) else []
            products = [mapper.map_to_standard("taobao", item) for item in items[: request.max_count]]
            return CrawlResult(products=products, total_found=len(products), crawl_duration_ms=0)
        except Exception as e:
            logger.warning("taobao_crawl_failed", error=str(e))
            return CrawlResult(products=[], total_found=0, crawl_duration_ms=0)
