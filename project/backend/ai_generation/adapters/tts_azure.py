from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import asyncio
from typing import Any

import httpx

from model_adapters.tts import BaseTTSAdapter
from model_adapters.base import UsageRecord
from model_adapters.cost import CostCalculator

from common_sdk.vault_client import vault_client

from ._minio_helper import upload_bytes


class AzureTTSAdapter(BaseTTSAdapter):
    protocol: str = "azure_cognitive"

    def __init__(
        self,
        adapter_id: str,
        model: str = "azure-tts",
        endpoint: str = "https://eastasia.tts.speech.microsoft.com",
        priority: int = 10,
        max_concurrency: int = 5,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            model=model,
            endpoint=endpoint,
            protocol="azure_cognitive",
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities or {},
        )
        self._cost_calc = CostCalculator()

    def _resolve_api_key(self) -> str:
        cred = vault_client.get_model_credential(self.adapter_id)
        return cred.get("api_key", "") if cred else ""

    async def synthesize_async(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh",
        speed: float = 1.0,
        tenant_id: str = "default",
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        ssml = f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{language}'>
            <voice name='{voice}'>
                <prosody rate='{speed}'>
                    {text}
                </prosody>
            </voice>
        </speak>"""

        api_key = self._resolve_api_key()
        headers = {
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
        }
        if api_key:
            headers["Ocp-Apim-Subscription-Key"] = api_key

        url = f"{self.endpoint.rstrip('/')}/cognitiveservices/v1"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, content=ssml, headers=headers)
                resp.raise_for_status()
                audio_bytes = resp.content
        except Exception:
            self.mark_failure()
            raise

        object_prefix = f"generated/audio/{tenant_id}/{pipeline_id}" if pipeline_id else f"generated/audio/{tenant_id}"
        audio_object = upload_bytes(audio_bytes, object_prefix, "audio/mpeg")

        cost = self._cost_calc.calculate_cost(
            adapter_type="tts",
            model=self.model,
            char_count=len(text),
        )
        record = UsageRecord(
            adapter_id=self.adapter_id,
            adapter_type="tts",
            model=self.model,
            duration_seconds=0,
            estimated_cost_usd=cost,
            status="success",
        )
        await self._cost_calc.log_usage_async(record)
        self.mark_success()
        return {"audio_object": audio_object}

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh",
        speed: float = 1.0,
        tenant_id: str = "default",
        pipeline_id: str = "",
    ) -> dict[str, Any]:
        return asyncio.run(self.synthesize_async(text, voice, language, speed, tenant_id, pipeline_id))

    def get_result(self, task_id: str) -> dict[str, Any]:
        return {"audio_object": ""}
