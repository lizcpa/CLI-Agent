from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from .models import PlatformAdapterConfig, PublishContent, PublishRequest, PublishResult

logger = logging.getLogger(__name__)


class BasePlatformPublisher(ABC):
    platform_id: str
    platform_config: PlatformAdapterConfig

    def __init__(self, platform_config: PlatformAdapterConfig) -> None:
        self.platform_id = platform_config.platform_id
        self.platform_config = platform_config
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._open_id: str = ""
        self._oauth_flow: Any = None

    @abstractmethod
    async def publish(self, request: PublishRequest) -> PublishResult:
        ...

    async def upload_video(self, video_url: str) -> str:
        raise NotImplementedError

    async def upload_cover(self, cover_url: str) -> str:
        raise NotImplementedError

    async def create_post(
        self,
        platform_video_id: str,
        cover_id: str,
        content: PublishContent,
    ) -> str:
        raise NotImplementedError

    def _get_oauth_flow(self) -> Any:
        if self._oauth_flow is None:
            from .oauth import OAuthFlow

            cfg = self.platform_config.config or {}
            self._oauth_flow = OAuthFlow(self.platform_id, cfg)
        return self._oauth_flow

    async def get_oauth_token(self) -> str:
        await self.refresh_token_if_needed()
        if self._access_token:
            return self._access_token
        return os.environ.get(f"{self.platform_id.upper()}_ACCESS_TOKEN", "")

    async def refresh_token_if_needed(self) -> None:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return
        try:
            from common_sdk.vault_client import vault_client

            tenant = self.platform_config.config.get("tenant_id", "default")
            refresh_token = await vault_client.get_platform_refresh_token(self.platform_id, tenant)
            if not refresh_token:
                return
            flow = self._get_oauth_flow()
            result = await flow.refresh(refresh_token)
            if not result.get("access_token"):
                return
            self._access_token = result["access_token"]
            self._token_expires_at = time.time() + int(result.get("expires_in") or 3600)
            if result.get("open_id"):
                self._open_id = result["open_id"]
            if result.get("refresh_token"):
                try:
                    await vault_client.store_platform_refresh_token(
                        self.platform_id,
                        tenant,
                        result["refresh_token"],
                        extra={
                            "open_id": result.get("open_id", ""),
                            "expires_in": result.get("expires_in", 3600),
                        },
                    )
                except Exception as e:
                    logger.warning("vault_store_refresh_failed", error=str(e))
        except Exception as e:
            logger.warning("refresh_token_failed", platform=self.platform_id, error=str(e))

    async def get_open_id(self) -> str:
        if self._open_id:
            return self._open_id
        return await self.get_platform_user_id_from_db() or ""

    async def get_platform_user_id_from_db(self) -> str | None:
        try:
            from db_clients.mysql import get_mysql_client

            mysql = get_mysql_client()
            tenant = self.platform_config.config.get("tenant_id", "default")
            row = await mysql.fetchone(
                "SELECT platform_user_id FROM platform_authorizations "
                "WHERE platform=%s AND tenant_id=%s AND status='active' LIMIT 1",
                (self.platform_id, tenant),
            )
            return row["platform_user_id"] if row else None
        except Exception as e:
            logger.warning("get_platform_user_id_failed", error=str(e))
            return None

    async def schedule_post(self, post_id: str, scheduled_time: datetime) -> None:
        raise NotImplementedError

    async def check_publish_status(self, platform_post_id: str) -> PublishResult:
        raise NotImplementedError
