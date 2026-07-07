from __future__ import annotations

import logging
from typing import Type

from .base_crawler import BasePlatformCrawler
from .base_publisher import BasePlatformPublisher
from .models import PlatformAdapterConfig

logger = logging.getLogger(__name__)

CrawlerClass = Type[BasePlatformCrawler]
PublisherClass = Type[BasePlatformPublisher]


class CrawlerRegistry:
    _crawlers: dict[str, CrawlerClass]
    _instances: dict[str, BasePlatformCrawler]
    _configs: dict[str, PlatformAdapterConfig]

    def __init__(self) -> None:
        self._crawlers = {}
        self._instances = {}
        self._configs = {}

    def register_crawler(self, platform_id: str, crawler_class: CrawlerClass) -> None:
        self._crawlers[platform_id] = crawler_class
        logger.info("Registered crawler: platform_id=%s class=%s", platform_id, crawler_class.__name__)

    def get_crawler(self, platform_id: str) -> BasePlatformCrawler | None:
        if platform_id in self._instances:
            return self._instances[platform_id]
        crawler_class = self._crawlers.get(platform_id)
        if crawler_class is None:
            logger.warning("No crawler registered for platform_id=%s", platform_id)
            return None
        config = self._configs.get(platform_id)
        if config is None:
            config = PlatformAdapterConfig(platform_id=platform_id, connector_class=crawler_class.__name__)
        instance = crawler_class(config)
        self._instances[platform_id] = instance
        return instance

    def list_platforms(self) -> list[str]:
        return list(self._crawlers.keys())

    def load_from_config(self, config_list: list[PlatformAdapterConfig]) -> None:
        for cfg in config_list:
            self._configs[cfg.platform_id] = cfg


class PublisherRegistry:
    _publishers: dict[str, PublisherClass]
    _instances: dict[str, BasePlatformPublisher]
    _configs: dict[str, PlatformAdapterConfig]

    def __init__(self) -> None:
        self._publishers = {}
        self._instances = {}
        self._configs = {}

    def register_publisher(self, platform_id: str, publisher_class: PublisherClass) -> None:
        self._publishers[platform_id] = publisher_class
        logger.info("Registered publisher: platform_id=%s class=%s", platform_id, publisher_class.__name__)

    def get_publisher(self, platform_id: str) -> BasePlatformPublisher | None:
        if platform_id in self._instances:
            return self._instances[platform_id]
        publisher_class = self._publishers.get(platform_id)
        if publisher_class is None:
            logger.warning("No publisher registered for platform_id=%s", platform_id)
            return None
        config = self._configs.get(platform_id)
        if config is None:
            config = PlatformAdapterConfig(platform_id=platform_id, connector_class=publisher_class.__name__)
        instance = publisher_class(config)
        self._instances[platform_id] = instance
        return instance

    def list_platforms(self) -> list[str]:
        return list(self._publishers.keys())

    def load_from_config(self, config_list: list[PlatformAdapterConfig]) -> None:
        for cfg in config_list:
            self._configs[cfg.platform_id] = cfg
