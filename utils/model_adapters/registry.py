from __future__ import annotations

from typing import Any

import yaml

from .base import BaseModelAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseModelAdapter] = {}

    def register(self, adapter: BaseModelAdapter) -> None:
        self._adapters[adapter.adapter_id] = adapter

    def unregister(self, adapter_id: str) -> None:
        self._adapters.pop(adapter_id, None)

    def get_adapter(self, adapter_id: str) -> BaseModelAdapter | None:
        return self._adapters.get(adapter_id)

    def list_adapters(self, adapter_type: str | None = None) -> list[BaseModelAdapter]:
        if adapter_type is None:
            return list(self._adapters.values())
        return [
            a for a in self._adapters.values() if a.adapter_type == adapter_type
        ]

    def get_healthy_adapters(self, adapter_type: str) -> list[BaseModelAdapter]:
        return [
            a
            for a in self._adapters.values()
            if a.adapter_type == adapter_type and a.can_accept()
        ]

    def to_config_yaml(self) -> str:
        config: dict[str, Any] = {"adapters": {}}
        for aid, adapter in self._adapters.items():
            config["adapters"][aid] = {
                "adapter_type": adapter.adapter_type,
                "model": adapter.model,
                "endpoint": adapter.endpoint,
                "protocol": adapter.protocol,
                "priority": adapter.priority,
                "max_concurrency": adapter.max_concurrency,
                "capabilities": adapter.capabilities,
            }
        return yaml.dump(config, default_flow_style=False, allow_unicode=True)


class ModelRouter:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    def route(
        self,
        adapter_type: str,
        product_tier: str | None = None,
        preferred_model: str | None = None,
    ) -> BaseModelAdapter | None:
        candidates = self._registry.list_adapters(adapter_type)
        if not candidates:
            return None

        healthy = [a for a in candidates if a.can_accept()]

        if not healthy:
            return None

        if preferred_model is not None:
            for adapter in healthy:
                if adapter.model == preferred_model:
                    return adapter
            return None

        if product_tier == "hot":
            healthy.sort(key=lambda a: a.priority)
            return healthy[0]

        if product_tier == "normal":
            free_or_low = [
                a
                for a in healthy
                if a.capabilities.get("cost_tag") in ("free", "low")
            ]
            if free_or_low:
                free_or_low.sort(key=lambda a: a.priority)
                return free_or_low[0]

        healthy.sort(key=lambda a: a.priority)
        return healthy[0]

    def track_failure(self, adapter_id: str) -> None:
        adapter = self._registry.get_adapter(adapter_id)
        if adapter:
            adapter.mark_failure()

    def track_success(self, adapter_id: str) -> None:
        adapter = self._registry.get_adapter(adapter_id)
        if adapter:
            adapter.mark_success()

    def get_status_hash(self, adapter_id: str) -> dict[str, str]:
        adapter = self._registry.get_adapter(adapter_id)
        if not adapter:
            return {"failure_count": "0", "degraded_until": ""}
        return {
            "failure_count": str(adapter.failure_count),
            "degraded_until": str(adapter.disabled_until) if adapter.disabled_until else "",
        }
