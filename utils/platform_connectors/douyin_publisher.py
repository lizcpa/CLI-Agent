from __future__ import annotations

import logging

import httpx

from .base_publisher import BasePlatformPublisher
from .mapper import resolve_jsonpath
from .models import PublishContent, PublishRequest, PublishResult

logger = logging.getLogger(__name__)


class DouyinPublisher(BasePlatformPublisher):
    """Douyin Open API publisher.

    Endpoint URLs and response JsonPath fields are read from platform_config
    (api_upload_url, api_publish_url, api_video_id_path, api_post_id_path).
    Inherits real token refresh from BasePlatformPublisher.
    """

    async def publish(self, request: PublishRequest) -> PublishResult:
        try:
            content = request.content
            video_id = await self.upload_video(content.video_url)
            cover_id = ""
            if content.cover_url:
                cover_id = await self.upload_cover(content.cover_url)
            post_id = await self.create_post(video_id, cover_id, content)
            return PublishResult(
                platform_post_id=post_id,
                status="published",
            )
        except Exception as e:
            logger.warning("douyin_publish_failed", error=str(e))
            return PublishResult(
                platform_post_id="",
                status="failed",
                error_message=str(e),
            )

    async def upload_video(self, video_url: str) -> str:
        cfg = self.platform_config.config
        upload_url = cfg.get(
            "api_upload_url",
            "https://open.douyin.com/api/douyin/v1/video/upload_video/",
        )
        access_token = await self.get_oauth_token()
        open_id = await self.get_open_id()
        async with httpx.AsyncClient(timeout=120) as client:
            video_resp = await client.get(video_url)
            video_resp.raise_for_status()
            files = {"video": ("video.mp4", video_resp.content, "video/mp4")}
            data = {"open_id": open_id}
            headers = {"Authorization": f"Bearer {access_token}"}
            resp = await client.post(upload_url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            body = resp.json()
        video_field = cfg.get("api_video_id_path", "data.video.video_id")
        return resolve_jsonpath(body, video_field) or ""

    async def upload_cover(self, cover_url: str) -> str:
        cfg = self.platform_config.config
        upload_url = cfg.get(
            "api_cover_upload_url",
            "https://open.douyin.com/api/douyin/v1/video/cover_upload/",
        )
        access_token = await self.get_oauth_token()
        open_id = await self.get_open_id()
        async with httpx.AsyncClient(timeout=60) as client:
            img_resp = await client.get(cover_url)
            img_resp.raise_for_status()
            files = {"image": ("cover.jpg", img_resp.content, "image/jpeg")}
            data = {"open_id": open_id}
            headers = {"Authorization": f"Bearer {access_token}"}
            resp = await client.post(upload_url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            body = resp.json()
        cover_field = cfg.get("api_cover_id_path", "data.image.image_id")
        return resolve_jsonpath(body, cover_field) or ""

    async def create_post(
        self,
        platform_video_id: str,
        cover_id: str,
        content: PublishContent,
    ) -> str:
        cfg = self.platform_config.config
        publish_url = cfg.get(
            "api_publish_url",
            "https://open.douyin.com/api/douyin/v1/video/publish_video/",
        )
        access_token = await self.get_oauth_token()
        open_id = await self.get_open_id()
        body = {
            "video_id": platform_video_id,
            "cover_id": cover_id,
            "text": content.description or content.title,
            "open_id": open_id,
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(publish_url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        post_field = cfg.get("api_post_id_path", "data.item_id")
        return resolve_jsonpath(data, post_field) or ""
