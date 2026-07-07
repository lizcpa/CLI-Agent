from __future__ import annotations

import os
from typing import Any


class ConfigManager:
    _instance: ConfigManager | None = None

    def __new__(cls) -> ConfigManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._load_dotenv()

    @staticmethod
    def _load_dotenv() -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

    def get(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        val = os.environ.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = os.environ.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes", "on")


config_manager = ConfigManager()
