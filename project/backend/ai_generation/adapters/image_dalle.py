from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import asyncio
from typing import Any

import httpx

from model_adapters.image import BaseImageAdapter
from model_adapters.base import UsageRecord
from model_adapters.cost import CostCalculator

from common_sdk.vault_client import vault_client

from ._minio_helper import upload_bytes


class DALLEImageAdapter(BaseImageAdapter):
    protocol: str = "openai_rest"

    def __init__(
        self,
        adapter_id: str,
        model: str = "dall-e-3",
        endpoint: str = "https://api.openai.com",
        priority: int = 10,
        max_concurrency: int = 2,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
            protocol="openai_rest",
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities or {},
        )
        self._cost_calc = CostCalculator()

    def _resolve_api_key(self) -> str:
        cred = vault_client.get_model_credential(self.adapter_id)
        return cred.get("api_key", "") if cred else ""

    async def generate_async(
        self,
        prompts: list[str],
        size: str = "1024x1024",
        n: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
        tenant_id: str = "default",
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        url = f"{self.endpoint.rstrip('/')}/v1/images/generations"
        api_key = self._resolve_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        main_prompt = prompts[0] if prompts else ""
        payload = {
            "model": self.model,
            "prompt": main_prompt,
            "n": n,
            "size": size,
        }

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                cloud_image_urls = [img.get("url", "") for img in data.get("data", [])]

                object_prefix = f"generated/images/{tenant_id}/{pipeline_id}" if pipeline_id else f"generated/images/{tenant_id}"
                image_objects: list[str] = []
                for cloud_url in cloud_image_urls:
                    if not cloud_url:
                        continue
                    img_resp = await client.get(cloud_url, timeout=120)
                    img_resp.raise_for_status()
                    obj = upload_bytes(img_resp.content, object_prefix, "image/png")
                    image_objects.append(obj)
        except Exception:
            self.mark_failure()
            raise

        cost = self._cost_calc.calculate_cost(
            adapter_type="image",
            model=self.model,
            image_count=n,
            resolution=size,
        )
        record = UsageRecord(
            adapter_id=self.adapter_id,
            adapter_type="image",
            model=self.model,
            image_count=n,
            estimated_cost_usd=cost,
            status="success",
        )
        await self._cost_calc.log_usage_async(record)
        self.mark_success()
        return {"image_objects": image_objects}

    def generate(
        self,
        prompts: list[str],
        size: str = "1024x1024",
        n: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
        tenant_id: str = "default",
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        return asyncio.run(self.generate_async(prompts, size, n, negative_prompt, seed, tenant_id, pipeline_id))

    def get_result(self, task_id: str) -> dict[str, Any]:
        return {"image_objects": []}
