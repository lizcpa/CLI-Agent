from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import asyncio
from typing import Any

import httpx

from model_adapters.llm import BaseLLMAdapter
from model_adapters.base import UsageRecord
from model_adapters.cost import CostCalculator

from common_sdk.vault_client import vault_client


class OpenAILLMAdapter(BaseLLMAdapter):
    protocol: str = "openai_rest"

    def __init__(
        self,
        adapter_id: str,
        model: str = "gpt-4o",
        endpoint: str = "https://api.openai.com",
        priority: int = 10,
        max_concurrency: int = 10,
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

    async def chat_async(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        url = f"{self.endpoint.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        api_key = self._resolve_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            self.mark_failure()
            raise

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = self._cost_calc.calculate_cost(
            adapter_type="llm",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        record = UsageRecord(
            adapter_id=self.adapter_id,
            adapter_type="llm",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            status="success",
        )
        await self._cost_calc.log_usage_async(record)
        self.mark_success()

        return {"text": data["choices"][0]["message"]["content"]}

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return asyncio.run(self.chat_async(messages, max_tokens, temperature))
