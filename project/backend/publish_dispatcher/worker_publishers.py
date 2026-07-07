from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from common_sdk.logger import get_logger
from platform_connectors import PlatformAdapterConfig, PublisherRegistry
from platform_connectors.douyin_publisher import DouyinPublisher
from platform_connectors.generic_http_publisher import GenericHTTPPublisher

logger = get_logger(__name__)

_PUBLISHER_CLASSES = {
    "douyin": DouyinPublisher,
    "youtube": GenericHTTPPublisher,
    "tiktok": GenericHTTPPublisher,
    "instagram": GenericHTTPPublisher,
}

worker_publishers = PublisherRegistry()
for _p, _cls in _PUBLISHER_CLASSES.items():
    worker_publishers.register_publisher(_p, _cls)

worker_publishers.load_from_config(
    [
        PlatformAdapterConfig(
            platform_id=p,
            connector_class=cls.__name__,
            config={"tenant_id": "default"},
        )
        for p, cls in _PUBLISHER_CLASSES.items()
    ]
)

logger.info("worker_publishers_initialized", platforms=worker_publishers.list_platforms())


async def load_worker_publisher_configs() -> None:
    """Load per-platform config from the platform_config table and inject into the registry.

    Fail-soft: on any error, the default configs registered above remain in place.
    """
    try:
        from db_clients.mysql import get_mysql_client

        mysql = get_mysql_client()
        rows = await mysql.fetchall(
            "SELECT platform, config_key, config_value FROM platform_config"
        )
        cfg_by_platform: dict[str, dict[str, str]] = {}
        for row in rows:
            cfg_by_platform.setdefault(row["platform"], {})[row["config_key"]] = row["config_value"]
        configs = []
        for platform, cfg in cfg_by_platform.items():
            cfg.setdefault("tenant_id", "default")
            configs.append(
                PlatformAdapterConfig(
                    platform_id=platform,
                    connector_class=cfg.get("connector_class", "GenericHTTPPublisher"),
                    config=cfg,
                )
            )
        if configs:
            worker_publishers.load_from_config(configs)
        logger.info("worker_publisher_configs_loaded", count=len(configs))
    except Exception as e:
        logger.warning("load_worker_publisher_configs_failed", error=str(e))


__all__ = ["worker_publishers", "load_worker_publisher_configs"]
