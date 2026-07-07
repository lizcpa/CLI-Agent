from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from model_adapters.base import BaseModelAdapter
from model_adapters.registry import AdapterRegistry

from common_sdk.registry_config import load_model_registry
from common_sdk.logger import get_logger

from .adapters import (
    OpenAILLMAdapter,
    ClaudeLLMAdapter,
    ComfyUIImageAdapter,
    DALLEImageAdapter,
    Veo3VideoAdapter,
    SoraVideoAdapter,
    AzureTTSAdapter,
)

logger = get_logger(__name__)


_ADAPTER_MAP: dict[tuple[str, str], type[BaseModelAdapter]] = {
    ("openai_rest", "llm"): OpenAILLMAdapter,
    ("anthropic_rest", "llm"): ClaudeLLMAdapter,
    ("comfyui_api", "image"): ComfyUIImageAdapter,
    ("openai_rest", "image"): DALLEImageAdapter,
    ("google_vertex", "video"): Veo3VideoAdapter,
    ("openai_rest", "video"): SoraVideoAdapter,
    ("azure_cognitive", "tts"): AzureTTSAdapter,
}


class RegistryManager:
    def __init__(self) -> None:
        self._registry = AdapterRegistry()

    @property
    def registry(self) -> AdapterRegistry:
        return self._registry

    def load_adapters(self) -> dict[str, list[dict]]:
        return load_model_registry()

    def register_default_adapters(self) -> None:
        for adapter_type, configs in self.load_adapters().items():
            for cfg in configs:
                try:
                    adapter = self._build_real_adapter(cfg)
                    self._registry.register(adapter)
                except Exception as e:
                    logger.warning(
                        "adapter_build_failed",
                        adapter_id=cfg.get("id"),
                        type=cfg.get("type"),
                        error=str(e),
                    )

    def _build_real_adapter(self, cfg: dict) -> BaseModelAdapter:
        cls = _ADAPTER_MAP.get((cfg.get("protocol", ""), cfg["type"]))
        if cls is None:
            raise ValueError(
                f"Unsupported adapter protocol={cfg.get('protocol')} type={cfg['type']}"
            )
        return cls(
            adapter_id=cfg["id"],
            model=cfg["id"],
            endpoint=cfg["endpoint"],
            priority=cfg.get("priority", 10),
            max_concurrency=cfg.get("max_concurrency", 5),
            capabilities=cfg.get("capabilities", {}),
        )
