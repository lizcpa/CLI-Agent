from __future__ import annotations

import logging
from typing import Any

from .models import StandardProduct

logger = logging.getLogger(__name__)


def resolve_jsonpath(data: dict[str, Any], path: str) -> Any:
    """Resolve a JsonPath-like expression (e.g. $.a.b or a.b) against a dict."""
    if not path:
        return None
    parts = path.strip("$.").split(".")
    current: Any = data
    for part in parts:
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


class PlatformDataMapper:
    _mappings: dict[str, dict[str, str]]
    _default_mapping: dict[str, str]

    def __init__(self, mapping_rules: dict[str, Any] | None = None) -> None:
        self._mappings = {}
        self._default_mapping = {
            "platform": "",
            "platform_product_id": "",
            "title": "",
            "description": "",
            "main_image_url": "",
            "image_urls": "",
            "price": "",
            "currency": "",
            "sales_count": "",
            "rating": "",
            "category": "",
            "tags": "",
        }
        if mapping_rules:
            for platform, rules in mapping_rules.items():
                self.load_mapping(platform, rules)
        else:
            self._mappings["default"] = dict(self._default_mapping)

    def _resolve_jsonpath(self, data: dict[str, Any], path: str) -> Any:
        return resolve_jsonpath(data, path)

    def load_mapping(self, platform: str, rules: dict[str, str] | None = None) -> dict[str, str]:
        if rules is not None:
            merged = {**self._default_mapping, **rules}
            self._mappings[platform] = merged
            return merged
        stored = self._mappings.get(platform)
        if stored is not None:
            return stored
        default = self._mappings.get("default", dict(self._default_mapping))
        default = {**default, "platform": platform}
        self._mappings[platform] = default
        return default

    def map_to_standard(self, platform: str, raw_data: dict[str, Any]) -> StandardProduct:
        mapping = self.load_mapping(platform)
        mapped: dict[str, Any] = {"platform": platform, "raw_data": raw_data}

        for field, path in mapping.items():
            if field in ("raw_data", "platform"):
                continue
            if not path:
                continue
            value = self._resolve_jsonpath(raw_data, path)
            if value is not None:
                mapped[field] = self._coerce_value(field, value)

        return StandardProduct(**mapped)

    def map_batch(self, platform: str, raw_data_list: list[dict[str, Any]]) -> list[StandardProduct]:
        return [self.map_to_standard(platform, raw) for raw in raw_data_list]

    @staticmethod
    def _coerce_value(field: str, value: Any) -> Any:
        if field in ("sales_count",):
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
        if field in ("price", "rating"):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None if field == "price" else 0.0
        if field in ("tags", "image_urls") and isinstance(value, list):
            return value
        if field in ("tags", "image_urls") and not isinstance(value, list):
            return [str(value)] if value else []
        return str(value)

    @staticmethod
    def _default_for_field(field: str) -> Any:
        defaults: dict[str, Any] = {
            "tags": [],
            "image_urls": [],
            "sales_count": 0,
            "rating": 0.0,
            "currency": "CNY",
            "price": None,
            "description": None,
            "main_image_url": None,
            "category": None,
        }
        return defaults.get(field, None)
