from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import asyncio
import time
from typing import Any

import httpx

from model_adapters.video import BaseVideoAdapter
from model_adapters.base import UsageRecord
from model_adapters.cost import CostCalculator

from common_sdk.vault_client import vault_client

from ._minio_helper import upload_bytes


class SoraVideoAdapter(BaseVideoAdapter):
    protocol: str = "openai_rest"

    def __init__(
        self,
        adapter_id: str,
        model: str = "sora-2",
        endpoint: str = "https://api.openai.com",
        priority: int = 8,
        max_concurrency: int = 1,
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

    def _report_progress(self, task_id: str | None, percent: int) -> None:
        if not task_id:
            return
        try:
            import redis as sync_redis
            r = sync_redis.Redis.from_url(
                "redis://:dev_redis_2024@localhost:6379/0", decode_responses=True
            )
            r.hset(f"task:{task_id}", mapping={"progress_percent": str(percent)})
        except Exception:
            pass

    async def generate_async(
        self,
        type: str,
        prompts: list[str],
        reference_image_url: str | None = None,
        duration: int = 5,
        resolution: str = "1080x1920",
        count: int = 1,
        motion_strength: float = 0.8,
        tenant_id: str = "default",
        pipeline_id: str = "",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.endpoint.rstrip('/')}/v1/video/generations"
        api_key = self._resolve_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": self.model,
            "prompt": prompts[0] if prompts else "",
            "duration": duration,
            "resolution": resolution,
            "n": count,
        }
        if reference_image_url:
            payload["image"] = reference_image_url

        try:
            async with httpx.AsyncClient(timeout=1800) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                generation_id = data.get("id", "")

                clip_objects = await self._poll_generation(
                    client, generation_id, tenant_id, pipeline_id, task_id
                )
        except Exception:
            self.mark_failure()
            raise

        cost = self._cost_calc.calculate_cost(
            adapter_type="video",
            model=self.model,
            duration_seconds=duration,
            count=count,
        )
        record = UsageRecord(
            adapter_id=self.adapter_id,
            adapter_type="video",
            model=self.model,
            duration_seconds=duration,
            estimated_cost_usd=cost,
            status="success",
        )
        await self._cost_calc.log_usage_async(record)
        self.mark_success()
        return {"clip_objects": clip_objects}

    async def _poll_generation(
        self,
        client: httpx.AsyncClient,
        generation_id: str,
        tenant_id: str,
        pipeline_id: str,
        task_id: str | None,
        max_wait: int = 600,
    ) -> list[str]:
        if not generation_id:
            raise RuntimeError("Sora /v1/video/generations returned no id")
        poll_url = f"{self.endpoint.rstrip('/')}/v1/video/generations/{generation_id}"
        object_prefix = f"generated/videos/{tenant_id}/{pipeline_id}" if pipeline_id else f"generated/videos/{tenant_id}"
        deadline = time.time() + max_wait
        delay = 10.0
        start = time.time()

        while time.time() < deadline:
            resp = await client.get(poll_url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")
            if status == "completed":
                videos = data.get("videos", [])
                clip_objects: list[str] = []
                for v in videos:
                    video_url = v.get("url", "")
                    if not video_url:
                        continue
                    vid_resp = await client.get(video_url, timeout=300)
                    vid_resp.raise_for_status()
                    obj = upload_bytes(vid_resp.content, object_prefix, "video/mp4")
                    clip_objects.append(obj)
                if clip_objects:
                    return clip_objects
                raise RuntimeError("Sora generation completed but no video URLs")
            if status == "failed":
                raise RuntimeError(f"Sora generation failed: {data.get('error', 'unknown')}")
            elapsed = int(time.time() - start)
            self._report_progress(task_id, 10 + int(80 * elapsed / max_wait))
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)
        raise TimeoutError(f"Sora generation {generation_id} did not complete within {max_wait}s")

    def generate(
        self,
        type: str,
        prompts: list[str],
        reference_image_url: str | None = None,
        duration: int = 5,
        resolution: str = "1080x1920",
        count: int = 1,
        motion_strength: float = 0.8,
        tenant_id: str = "default",
        pipeline_id: str = "",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(self.generate_async(type, prompts, reference_image_url, duration, resolution, count, motion_strength, tenant_id, pipeline_id, task_id))

    def get_result(self, task_id: str) -> dict[str, Any]:
        return {"clip_objects": []}
