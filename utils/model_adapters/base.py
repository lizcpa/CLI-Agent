from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from common_sdk.resilience import CircuitBreaker


@dataclass
class UsageRecord:
    adapter_id: str
    adapter_type: str
    model: str
    pipeline_id: str = ""
    tenant_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0
    duration_seconds: float = 0.0
    estimated_cost_usd: float = 0.0
    status: str = "success"


class BaseModelAdapter(ABC):
    adapter_id: str
    adapter_type: str
    model: str
    endpoint: str
    protocol: str
    priority: int
    max_concurrency: int
    capabilities: dict[str, Any]
    is_healthy: bool
    failure_count: int
    disabled_until: float | None

    _DEGRADATION_THRESHOLD = 3
    _COOLDOWN_SECONDS = 300

    def __init__(
        self,
        adapter_id: str,
        adapter_type: str,
        model: str,
        endpoint: str,
        protocol: str = "openai_rest",
        priority: int = 100,
        max_concurrency: int = 10,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        self.adapter_id = adapter_id
        self.adapter_type = adapter_type
        self.model = model
        self.endpoint = endpoint
        self.protocol = protocol
        self.priority = priority
        self.max_concurrency = max_concurrency
        self.capabilities = capabilities or {}
        self.is_healthy = True
        self.failure_count = 0
        self.disabled_until = None
        self._breaker = CircuitBreaker(
            name=adapter_id,
            failure_threshold=self._DEGRADATION_THRESHOLD,
            cooldown_seconds=self._COOLDOWN_SECONDS,
        )

    @abstractmethod
    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def check_health(self) -> bool:
        ...

    def mark_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self._DEGRADATION_THRESHOLD:
            self.is_healthy = False
            self.disabled_until = time.time() + self._COOLDOWN_SECONDS
        self._breaker.record_failure()

    def mark_success(self) -> None:
        self.failure_count = 0
        self.is_healthy = True
        self.disabled_until = None
        self._breaker.record_success()

    def can_accept(self) -> bool:
        if not self.is_healthy:
            return False
        if self.disabled_until is not None and time.time() < self.disabled_until:
            return False
        if self.disabled_until is not None and time.time() >= self.disabled_until:
            self.disabled_until = None
        return True
