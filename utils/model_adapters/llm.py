from __future__ import annotations

from abc import abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .base import BaseModelAdapter


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]
    max_tokens: int = 2048
    temperature: float = 0.7
    model: str | None = None


class ChatResponse(BaseModel):
    text: str


class BaseLLMAdapter(BaseModelAdapter):
    adapter_type: str = "llm"
    protocol: str = "openai_rest"

    def __init__(
        self,
        adapter_id: str,
        model: str,
        endpoint: str,
        protocol: str = "openai_rest",
        priority: int = 100,
        max_concurrency: int = 10,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            adapter_type="llm",
            model=model,
            endpoint=endpoint,
            protocol=protocol,
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities,
        )
        self._internal_endpoint = self.endpoint.rstrip("/") + "/api/v1/internal/llm/chat"

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        ...

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.chat(
            messages=request.get("messages", []),
            max_tokens=request.get("max_tokens", 2048),
            temperature=request.get("temperature", 0.7),
        )

    def check_health(self) -> bool:
        try:
            resp = httpx.get(
                self._internal_endpoint.replace("/chat", "/health"),
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


class OpenAILLMAdapter(BaseLLMAdapter):
    protocol: str = "openai_rest"

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        payload = ChatRequest(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=self.model,
        )
        resp = httpx.post(
            self._internal_endpoint,
            json=payload.model_dump(),
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"text": data.get("text", "")}


class ClaudeLLMAdapter(BaseLLMAdapter):
    protocol: str = "anthropic_rest"

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        payload = ChatRequest(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=self.model,
        )
        resp = httpx.post(
            self._internal_endpoint,
            json=payload.model_dump(),
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"text": data.get("text", "")}


class LocalLLMAdapter(BaseLLMAdapter):
    protocol: str = "local_rest"

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        payload = ChatRequest(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            model=self.model,
        )
        resp = httpx.post(
            self._internal_endpoint,
            json=payload.model_dump(),
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"text": data.get("text", "")}
