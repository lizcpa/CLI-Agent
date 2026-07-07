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
from common_sdk.logger import get_logger

from ._minio_helper import upload_bytes

logger = get_logger(__name__)


class Veo3VideoAdapter(BaseVideoAdapter):
    protocol: str = "google_vertex"

    def __init__(
        self,
        adapter_id: str,
        model: str = "veo-3.0-generate-preview",
        endpoint: str = "https://videogeneration.googleapis.com",
        priority: int = 10,
        max_concurrency: int = 2,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
            protocol="google_vertex",
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities or {},
        )
        self._cost_calc = CostCalculator()

    def _resolve_token(self) -> str:
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
        url = f"{self.endpoint.rstrip('/')}/v1/projects/-/locations/us-central1/publishers/google/models/veo-3.0-generate-preview:predictLongRunning"
        token = self._resolve_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        payload = {
            "instances": [{
                "prompt": prompts[0] if prompts else "",
                "durationSeconds": duration,
                "resolution": resolution,
                "numberOfVideos": count,
            }]
        }

        try:
            async with httpx.AsyncClient(timeout=1800) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                operation_name = data.get("name", "")

                clip_objects = await self._poll_operation(
                    client, operation_name, tenant_id, pipeline_id, task_id
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

    async def _poll_operation(
        self,
        client: httpx.AsyncClient,
        operation_name: str,
        tenant_id: str,
        pipeline_id: str,
        task_id: str | None,
        max_wait: int = 600,
    ) -> list[str]:
        if not operation_name:
            raise RuntimeError("Veo3 predictLongRunning returned no operation name")
        op_url = f"{self.endpoint.rstrip('/')}/v1/{operation_name}"
        object_prefix = f"generated/videos/{tenant_id}/{pipeline_id}" if pipeline_id else f"generated/videos/{tenant_id}"
        deadline = time.time() + max_wait
        delay = 10.0
        start = time.time()

        while time.time() < deadline:
            resp = await client.get(op_url, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if data.get("done", False):
                response = data.get("response", {})
                videos = response.get("videos", [])
                clip_objects: list[str] = []
                for v in videos:
                    video_uri = v.get("uri", "")
                    if not video_uri:
                        continue
                    vid_resp = await client.get(video_uri, timeout=300)
                    vid_resp.raise_for_status()
                    obj = upload_bytes(vid_resp.content, object_prefix, "video/mp4")
                    clip_objects.append(obj)
                if clip_objects:
                    return clip_objects
                raise RuntimeError("Veo3 operation done but no video URIs")
            elapsed = int(time.time() - start)
            self._report_progress(task_id, 10 + int(80 * elapsed / max_wait))
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)
        raise TimeoutError(f"Veo3 operation {operation_name} did not complete within {max_wait}s")

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
