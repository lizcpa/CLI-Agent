from __future__ import annotations

from typing import Any, Callable

import yaml

from .config import config_manager
from .logger import get_logger

logger = get_logger(__name__)


class NacosConfigProvider:
    _instance: NacosConfigProvider | None = None

    def __new__(cls) -> NacosConfigProvider:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._client: Any = None
        self._enabled = config_manager.get_bool("NACOS_ENABLED", True)
        self._server_addr = config_manager.get("NACOS_SERVER_ADDR", "localhost:8848")
        self._namespace = config_manager.get("NACOS_NAMESPACE", "prodvideo")
        self._group = config_manager.get("NACOS_GROUP", "DEFAULT_GROUP")
        self._connected = False

    def connect(self) -> NacosConfigProvider:
        if not self._enabled or self._connected:
            return self
        try:
            import nacos

            self._client = nacos.NacosClient(
                server_addresses=self._server_addr,
                namespace=self._namespace,
            )
            self._connected = True
            logger.info(
                "nacos_connected",
                server=self._server_addr,
                namespace=self._namespace,
            )
        except Exception as e:
            logger.warning("nacos_connect_failed", error=str(e))
            self._connected = False
        return self

    def _ensure_connected(self) -> bool:
        if not self._enabled:
            return False
        if not self._connected:
            self.connect()
        return self._connected and self._client is not None

    def get_config(self, data_id: str, default: str = "") -> str:
        if not self._ensure_connected():
            return default
        try:
            raw = self._client.get_config(data_id, self._group)
            return raw if raw else default
        except Exception as e:
            logger.warning("nacos_get_config_failed", data_id=data_id, error=str(e))
            return default

    def get_yaml(self, data_id: str, default: Any = None) -> Any:
        raw = self.get_config(data_id, None)
        if not raw:
            return default
        try:
            return yaml.safe_load(raw)
        except Exception as e:
            logger.warning("nacos_yaml_parse_failed", data_id=data_id, error=str(e))
            return default

    def add_watcher(self, data_id: str, callback: Callable[[str], None]) -> None:
        if not self._ensure_connected():
            return
        try:
            self._client.add_config_watcher(data_id, self._group, callback)
        except Exception as e:
            logger.warning("nacos_add_watcher_failed", data_id=data_id, error=str(e))


nacos_provider = NacosConfigProvider()
