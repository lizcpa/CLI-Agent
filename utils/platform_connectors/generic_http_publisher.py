from __future__ import annotations

import logging

import httpx

from .base_publisher import BasePlatformPublisher
from .models import PublishRequest, PublishResult

logger = logging.getLogger(__name__)


class GenericHTTPPublisher(BasePlatformPublisher):
    """Generic HTTP publisher. Endpoint URLs are read from platform_config table
    (api_upload_url, api_publish_url) and injected via PlatformAdapterConfig.config.
    """

    async def publish(self, request: PublishRequest) -> PublishResult:
        cfg = self.platform_config.config
        token = await self.get_oauth_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async with httpx.AsyncClient(timeout=300) as client:
            upload_resp = await client.post(
                cfg["api_upload_url"],
                headers=headers,
                json={
                    "video_url": request.content.video_url,
                    "cover_url": request.content.cover_url,
                },
            )
            upload_resp.raise_for_status()
            platform_video_id = upload_resp.json().get("video_id", "")

            publish_resp = await client.post(
                cfg["api_publish_url"],
                headers=headers,
                json={
                    "video_id": platform_video_id,
                    "title": request.content.title,
                    "description": request.content.description,
                    "tags": request.content.tags,
                    "product_link": request.content.product_link,
                },
            )
            publish_resp.raise_for_status()
            data = publish_resp.json()
            return PublishResult(
                platform_post_id=data.get("post_id", ""),
                public_url=data.get("public_url", ""),
                status="published",
            )

    async def refresh_token_if_needed(self) -> None:
        pass
