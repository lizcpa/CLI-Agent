from __future__ import annotations

import logging

from platform_connectors.models import PlatformAdapterConfig
from platform_connectors.registry import CrawlerRegistry

from .amazon_crawler import AmazonCrawler
from .douyin_crawler import DouyinCrawler
from .shopee_crawler import ShopeeCrawler
from .taobao_crawler import TaobaoCrawler

logger = logging.getLogger(__name__)

_CRAWLER_CLASSES = {
    "douyin": DouyinCrawler,
    "taobao": TaobaoCrawler,
    "amazon": AmazonCrawler,
    "shopee": ShopeeCrawler,
}


def build_crawler_registry(
    configs: dict[str, PlatformAdapterConfig] | None = None,
) -> CrawlerRegistry:
    reg = CrawlerRegistry()
    for platform, cls in _CRAWLER_CLASSES.items():
        reg.register_crawler(platform, cls)
    if configs:
        reg.load_from_config(list(configs.values()))
    return reg
