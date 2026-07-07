from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import config_manager
from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class ContentSafetyResult:
    passed: bool
    risk_level: str  # none / low / medium / high
    detail: str = ""


class ContentSafetyClient:
    _instance: ContentSafetyClient | None = None

    def __new__(cls) -> ContentSafetyClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._enabled = config_manager.get_bool("CONTENT_SAFETY_ENABLED", False)
        self._fail_closed = config_manager.get_bool("CONTENT_SAFETY_FAIL_CLOSED", False)
        self._access_key_id = config_manager.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        self._access_key_secret = config_manager.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        self._endpoint = config_manager.get(
            "CONTENT_SAFETY_ENDPOINT", "green.cn-shanghai.aliyuncs.com"
        )

    def _disabled_result(self) -> ContentSafetyResult:
        if self._fail_closed:
            return ContentSafetyResult(
                passed=False, risk_level="high", detail="content safety disabled (fail-closed)"
            )
        return ContentSafetyResult(
            passed=True, risk_level="none", detail="content safety disabled (fail-open)"
        )

    def _failed_result(self, error: str) -> ContentSafetyResult:
        if self._fail_closed:
            return ContentSafetyResult(passed=False, risk_level="high", detail=error)
        return ContentSafetyResult(
            passed=True, risk_level="none", detail=f"check failed (fail-open): {error}"
        )

    def check_text(self, text: str) -> ContentSafetyResult:
        if not self._enabled:
            return self._disabled_result()
        try:
            return self._call_text_api(text)
        except Exception as e:
            logger.warning("content_safety_text_failed", error=str(e))
            return self._failed_result(str(e))

    def check_image(self, image_url: str) -> ContentSafetyResult:
        if not self._enabled:
            return self._disabled_result()
        try:
            return self._call_image_api(image_url)
        except Exception as e:
            logger.warning("content_safety_image_failed", error=str(e))
            return self._failed_result(str(e))

    async def check_video_async(self, video_url: str) -> ContentSafetyResult:
        if not self._enabled:
            return self._disabled_result()
        try:
            return await self._call_video_api(video_url)
        except Exception as e:
            logger.warning("content_safety_video_failed", error=str(e))
            return self._failed_result(str(e))

    def _build_client(self) -> Any:
        from alibabacloud_green20220302.client import Client as GreenClient
        from alibabacloud_tea_openapi import models as open_api_models

        config = open_api_models.Config(
            access_key_id=self._access_key_id,
            access_key_secret=self._access_key_secret,
            endpoint=self._endpoint,
            read_timeout=30000,
            connect_timeout=30000,
        )
        return GreenClient(config)

    def _call_text_api(self, text: str) -> ContentSafetyResult:
        from alibabacloud_green20220302 import models as green_models

        client = self._build_client()
        req = green_models.TextModerationRequest(
            service="comment_detection",
            parameters={"content": text},
        )
        resp = client.text_moderation(req)
        body = resp.body
        if body.code == 200 and body.data:
            advice = body.data.get("advice", {})
            result_str = advice.get("result", "pass") if isinstance(advice, dict) else "pass"
            risk_level = "none" if result_str in ("pass", "review") else "high"
            return ContentSafetyResult(
                passed=risk_level != "high",
                risk_level=risk_level,
                detail=f"text checked: {result_str}",
            )
        return ContentSafetyResult(passed=True, risk_level="none", detail="text checked: pass")

    def _call_image_api(self, image_url: str) -> ContentSafetyResult:
        from alibabacloud_green20220302 import models as green_models

        client = self._build_client()
        req = green_models.ImageModerationRequest(
            service="baselineCheck",
            url=green_models.ImageModerationRequestURL(image_url),
        )
        resp = client.image_moderation(req)
        body = resp.body
        if body.code == 200 and body.data:
            results = body.data.get("result", []) if isinstance(body.data, dict) else []
            has_high = any(
                (r.get("confidence", 0) > 90 and r.get("label") not in ("nonLabel", "normal"))
                for r in results
                if isinstance(r, dict)
            )
            risk_level = "high" if has_high else "none"
            return ContentSafetyResult(
                passed=not has_high, risk_level=risk_level, detail=f"image checked: {len(results)} labels"
            )
        return ContentSafetyResult(passed=True, risk_level="none", detail="image checked: pass")

    async def _call_video_api(self, video_url: str) -> ContentSafetyResult:
        import asyncio

        return await asyncio.to_thread(self._call_video_sync, video_url)

    def _call_video_sync(self, video_url: str) -> ContentSafetyResult:
        from alibabacloud_green20220302 import models as green_models

        client = self._build_client()
        req = green_models.VideoModerationRequest(
            service="videoScan",
            url=video_url,
        )
        resp = client.video_moderation(req)
        body = resp.body
        if body.code == 200 and body.data:
            task_id = body.data.get("taskId", "") if isinstance(body.data, dict) else ""
            if task_id:
                import time

                for _ in range(30):
                    time.sleep(2)
                    result_req = green_models.VideoModerationResultRequest(task_id=task_id)
                    result_resp = client.video_moderation_result(result_req)
                    rbody = result_resp.body
                    if rbody.code == 200 and rbody.data:
                        state = rbody.data.get("taskId", {}).get("status", "") if isinstance(rbody.data, dict) else ""
                        if state in ("Succeed", "Success"):
                            risk_level = "none"
                            return ContentSafetyResult(
                                passed=True, risk_level=risk_level, detail="video checked: pass"
                            )
                return ContentSafetyResult(
                    passed=True, risk_level="none", detail="video check timeout (fail-open)"
                )
        return ContentSafetyResult(passed=True, risk_level="none", detail="video checked: pass")


content_safety_client = ContentSafetyClient()
