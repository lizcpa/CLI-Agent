from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel

from .base import UsageRecord

logger = logging.getLogger(__name__)


_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"input_per_1k": 0.005, "output_per_1k": 0.015},
    "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
    "gpt-4-turbo": {"input_per_1k": 0.01, "output_per_1k": 0.03},
    "claude-3-opus": {"input_per_1k": 0.015, "output_per_1k": 0.075},
    "claude-3-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "claude-3-haiku": {"input_per_1k": 0.00025, "output_per_1k": 0.00125},
    "claude-3.5-sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "dall-e-3": {"per_image_1024": 0.04, "per_image_1792": 0.08},
    "dall-e-2": {"per_image_1024": 0.02},
    "sdxl": {"per_image": 0.002},
    "stable-diffusion-3": {"per_image": 0.004},
    "sora": {"per_second": 0.02},
    "runway-gen3": {"per_second": 0.05},
    "kling": {"per_second": 0.03},
    "openai-tts": {"per_1k_char": 0.015},
    "openai-tts-hd": {"per_1k_char": 0.03},
    "elevenlabs": {"per_1k_char": 0.005},
    "edge-tts": {"per_1k_char": 0.0},
}


class UsageLogRequest(BaseModel):
    adapter_id: str
    adapter_type: str
    model: str
    pipeline_id: str
    tenant_id: str
    input_tokens: int
    output_tokens: int
    image_count: int
    duration_seconds: float
    estimated_cost_usd: float
    status: str


class CostCalculator:
    _INTERNAL_USAGE_ENDPOINT = "/api/v1/internal/usage/log"

    def calculate_cost(
        self,
        adapter_type: str,
        model: str,
        **kwargs: Any,
    ) -> float:
        prices = _PRICE_TABLE.get(model, {})
        if not prices:
            return 0.0

        if adapter_type == "llm":
            input_tokens = kwargs.get("input_tokens", 0)
            output_tokens = kwargs.get("output_tokens", 0)
            cost = (input_tokens / 1000) * prices.get("input_per_1k", 0)
            cost += (output_tokens / 1000) * prices.get("output_per_1k", 0)
            return round(cost, 6)

        if adapter_type == "image":
            image_count = kwargs.get("image_count", 1)
            resolution = kwargs.get("resolution", "1024x1024")
            if "1792" in resolution:
                return round(image_count * prices.get("per_image_1792", prices.get("per_image", 0.04)), 6)
            return round(image_count * prices.get("per_image_1024", prices.get("per_image", 0.02)), 6)

        if adapter_type == "video":
            duration = kwargs.get("duration_seconds", 5)
            count = kwargs.get("count", 1)
            return round(duration * count * prices.get("per_second", 0.02), 6)

        if adapter_type == "tts":
            char_count = kwargs.get("char_count", 0)
            return round((char_count / 1000) * prices.get("per_1k_char", 0.015), 6)

        return 0.0

    async def log_usage_async(self, usage_record: UsageRecord) -> None:
        """直接写 MySQL model_usage_log 表（fail-soft，不阻塞主流程）。"""
        from db_clients.mysql import get_mysql_client

        sql = (
            "INSERT INTO model_usage_log "
            "(adapter_id, adapter_type, model, pipeline_id, tenant_id, "
            " input_tokens, output_tokens, image_count, duration_seconds, "
            " estimated_cost, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        params = (
            usage_record.adapter_id,
            usage_record.adapter_type,
            usage_record.model,
            usage_record.pipeline_id,
            usage_record.tenant_id,
            usage_record.input_tokens,
            usage_record.output_tokens,
            usage_record.image_count,
            usage_record.duration_seconds,
            usage_record.estimated_cost_usd,
            usage_record.status,
        )
        try:
            mysql = get_mysql_client()
            await mysql.execute(sql, params)
        except Exception as e:
            logger.warning("log_usage_async failed: %s", e)

    def log_usage(
        self,
        usage_record: UsageRecord,
        endpoint: str | None = None,
    ) -> None:
        """已弃用：原 HTTP POST 路径。保留仅为向后兼容，内部转异步（仅无事件循环时可用）。"""
        import warnings

        warnings.warn(
            "log_usage is deprecated, use log_usage_async",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            asyncio.run(self.log_usage_async(usage_record))
        except RuntimeError:
            logger.warning("log_usage called inside running event loop; skipped. Use log_usage_async.")
        except Exception as e:
            logger.warning("log_usage fallback failed: %s", e)
