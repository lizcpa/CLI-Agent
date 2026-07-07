from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from model_adapters.base import BaseModelAdapter
from model_adapters.registry import AdapterRegistry, ModelRouter


class ModelRouterService:
    def __init__(self, registry: AdapterRegistry) -> None:
        self.registry = registry
        self.router = ModelRouter(registry)

    def route_llm(
        self,
        preferred_model: str | None = None,
        product_tier: str = "normal",
    ) -> BaseModelAdapter | None:
        return self.router.route("llm", product_tier, preferred_model)

    def route_image(
        self,
        preferred_model: str | None = None,
        product_tier: str = "normal",
    ) -> BaseModelAdapter | None:
        return self.router.route("image", product_tier, preferred_model)

    def route_video(
        self,
        preferred_model: str | None = None,
        product_tier: str = "normal",
    ) -> BaseModelAdapter | None:
        return self.router.route("video", product_tier, preferred_model)

    def route_tts(
        self,
        preferred_model: str | None = None,
    ) -> BaseModelAdapter | None:
        return self.router.route("tts", None, preferred_model)

    def get_available_models(self, adapter_type: str) -> list[dict]:
        adapters = self.registry.get_healthy_adapters(adapter_type)
        return [
            {
                "id": a.adapter_id,
                "type": a.adapter_type,
                "name": a.adapter_id,
                "is_healthy": a.is_healthy,
                "capabilities": a.capabilities,
            }
            for a in adapters
        ]
