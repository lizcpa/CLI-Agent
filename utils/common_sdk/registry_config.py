from __future__ import annotations

from typing import Any

from .nacos_client import nacos_provider

_MODEL_REGISTRY_DATA_ID = "model-adapters.yaml"
_PLATFORM_CONNECTORS_DATA_ID = "platform-connectors.yaml"

_MODEL_REGISTRY_FALLBACK: dict[str, list[dict]] = {
    "image": [
        {
            "id": "comfyui_sdxl",
            "type": "image",
            "endpoint": "http://localhost:8188",
            "protocol": "comfyui_api",
            "priority": 10,
            "max_concurrency": 5,
            "capabilities": {"max_size": "2048x2048"},
        },
        {
            "id": "dalle3",
            "type": "image",
            "endpoint": "https://api.openai.com",
            "protocol": "openai_rest",
            "priority": 10,
            "max_concurrency": 2,
            "capabilities": {"max_size": "1024x1024"},
        },
    ],
    "video": [
        {
            "id": "veo3",
            "type": "video",
            "endpoint": "https://videogeneration.googleapis.com",
            "protocol": "google_vertex",
            "priority": 10,
            "max_concurrency": 2,
            "capabilities": {"max_duration": 10, "max_resolution": "1080p"},
        },
        {
            "id": "sora",
            "type": "video",
            "endpoint": "https://api.openai.com",
            "protocol": "openai_rest",
            "priority": 8,
            "max_concurrency": 1,
            "capabilities": {"max_duration": 15, "max_resolution": "1080p"},
        },
    ],
    "llm": [
        {
            "id": "openai_gpt4o",
            "type": "llm",
            "endpoint": "https://api.openai.com",
            "protocol": "openai_rest",
            "priority": 10,
            "max_concurrency": 10,
            "capabilities": {},
        },
        {
            "id": "claude_sonnet",
            "type": "llm",
            "endpoint": "https://api.anthropic.com",
            "protocol": "anthropic_rest",
            "priority": 9,
            "max_concurrency": 5,
            "capabilities": {},
        },
    ],
    "tts": [
        {
            "id": "azure_tts",
            "type": "tts",
            "endpoint": "https://eastasia.tts.speech.microsoft.com",
            "protocol": "azure_cognitive",
            "priority": 10,
            "max_concurrency": 5,
            "capabilities": {},
        },
    ],
}

_PLATFORM_CONNECTORS_FALLBACK: list[dict] = [
    {
        "platform_id": "douyin",
        "connector_class": "platform_connectors.douyin.DouyinConnector",
        "proxy_required": True,
        "rate_limit": "10/min",
    },
    {
        "platform_id": "taobao",
        "connector_class": "platform_connectors.taobao.TaobaoConnector",
        "proxy_required": False,
        "rate_limit": "30/min",
    },
    {
        "platform_id": "amazon",
        "connector_class": "platform_connectors.amazon.AmazonConnector",
        "proxy_required": False,
        "rate_limit": "20/min",
    },
    {
        "platform_id": "shopee",
        "connector_class": "platform_connectors.shopee.ShopeeConnector",
        "proxy_required": True,
        "rate_limit": "15/min",
    },
]


def load_model_registry() -> dict[str, list[dict]]:
    data = nacos_provider.get_yaml(_MODEL_REGISTRY_DATA_ID, default=_MODEL_REGISTRY_FALLBACK)
    if not isinstance(data, dict):
        return _MODEL_REGISTRY_FALLBACK
    return data


def load_platform_connectors() -> list[dict]:
    data = nacos_provider.get_yaml(_PLATFORM_CONNECTORS_DATA_ID, default=None)
    if isinstance(data, dict) and "platforms" in data:
        platforms = data["platforms"]
        if isinstance(platforms, list):
            return platforms
    if isinstance(data, list):
        return data
    return _PLATFORM_CONNECTORS_FALLBACK
