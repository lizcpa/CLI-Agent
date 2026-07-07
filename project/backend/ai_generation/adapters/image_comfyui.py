from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import asyncio
import random
import time
from typing import Any

import httpx

from model_adapters.image import BaseImageAdapter
from model_adapters.base import UsageRecord
from model_adapters.cost import CostCalculator

from ._minio_helper import upload_bytes


class ComfyUIImageAdapter(BaseImageAdapter):
    protocol: str = "comfyui_api"

    def __init__(
        self,
        adapter_id: str,
        model: str = "sdxl",
        endpoint: str = "http://localhost:8188",
        priority: int = 10,
        max_concurrency: int = 5,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
            protocol="comfyui_api",
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities or {},
        )
        self._cost_calc = CostCalculator()

    async def generate_async(
        self,
        prompts: list[str],
        size: str = "1024x1024",
        n: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
        tenant_id: str = "default",
        pipeline_id: str = "",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.endpoint.rstrip('/')}/prompt"
        workflow = self._build_workflow(prompts, size, n, negative_prompt, seed)
        payload = {"prompt": workflow, "client_id": self.adapter_id}

        try:
            async with httpx.AsyncClient(timeout=600) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                prompt_id = data.get("prompt_id", "")

                image_objects = await self._poll_and_download(client, prompt_id, tenant_id, pipeline_id)
        except Exception:
            self.mark_failure()
            raise

        cost = self._cost_calc.calculate_cost(
            adapter_type="image", model=self.model, image_count=n
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

    async def _poll_and_download(
        self,
        client: httpx.AsyncClient,
        prompt_id: str,
        tenant_id: str,
        pipeline_id: str,
        max_wait: int = 600,
    ) -> list[str]:
        history_url = f"{self.endpoint.rstrip('/')}/history/{prompt_id}"
        object_prefix = f"generated/images/{tenant_id}/{pipeline_id}" if pipeline_id else f"generated/images/{tenant_id}"
        deadline = time.time() + max_wait
        delay = 2.0

        while time.time() < deadline:
            resp = await client.get(history_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                task_data = data.get(prompt_id, {})
                if task_data:
                    outputs = task_data.get("outputs", {})
                    image_objects: list[str] = []
                    for node_id, node_output in outputs.items():
                        for img in node_output.get("images", []):
                            filename = img.get("filename", "")
                            if not filename:
                                continue
                            view_url = f"{self.endpoint.rstrip('/')}/view?filename={filename}"
                            img_resp = await client.get(view_url, timeout=120)
                            img_resp.raise_for_status()
                            obj = upload_bytes(img_resp.content, object_prefix, "image/png")
                            image_objects.append(obj)
                    if image_objects:
                        return image_objects
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 15.0)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} did not complete within {max_wait}s")

    def _build_workflow(
        self,
        prompts: list[str],
        size: str,
        n: int,
        negative_prompt: str | None,
        seed: int | None,
    ) -> dict:
        parts = size.split("x")
        width = int(parts[0]) if len(parts) == 2 else 1024
        height = int(parts[1]) if len(parts) == 2 else 1024
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        ckpt = self.capabilities.get("ckpt", "sd_xl_base_1.0.safetensors")
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": actual_seed, "steps": 20, "cfg": 7,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0],
                "negative": ["7", 0], "latent_image": ["5", 0],
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": n}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompts[0] if prompts else "", "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "", "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
        }

    def generate(
        self,
        prompts: list[str],
        size: str = "1024x1024",
        n: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
        tenant_id: str = "default",
        pipeline_id: str = "",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return asyncio.run(self.generate_async(prompts, size, n, negative_prompt, seed, tenant_id, pipeline_id, task_id))

    def get_result(self, task_id: str) -> dict[str, Any]:
        return {"image_objects": []}
