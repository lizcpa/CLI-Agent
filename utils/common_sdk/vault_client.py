from __future__ import annotations

from typing import Any

from .config import config_manager
from .logger import get_logger

logger = get_logger(__name__)

_ENV_MAP: dict[str, str | None] = {
    "veo3": "GOOGLE_ACCESS_TOKEN",
    "sora": "OPENAI_API_KEY",
    "openai_gpt4o": "OPENAI_API_KEY",
    "dalle3": "OPENAI_API_KEY",
    "claude_sonnet": "ANTHROPIC_API_KEY",
    "azure_tts": "AZURE_SPEECH_KEY",
    "comfyui_sdxl": None,
}


class VaultClient:
    _instance: VaultClient | None = None

    def __new__(cls) -> VaultClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._client: Any = None
        self._enabled = config_manager.get_bool("VAULT_ENABLED", True)
        self._url = config_manager.get("VAULT_ADDR", "http://localhost:8200")
        self._token = config_manager.get("VAULT_TOKEN", "root")
        self._mount = config_manager.get("VAULT_MOUNT", "secret")
        self._connected = False

    def connect(self) -> VaultClient:
        if not self._enabled or self._connected:
            return self
        try:
            import hvac

            self._client = hvac.Client(url=self._url, token=self._token)
            if not self._client.is_authenticated():
                logger.warning("vault_auth_failed", url=self._url)
                self._connected = False
                return self
            self._connected = True
            logger.info("vault_connected", url=self._url, mount=self._mount)
        except Exception as e:
            logger.warning("vault_connect_failed", error=str(e))
            self._connected = False
        return self

    def _ensure(self) -> bool:
        if not self._enabled:
            return False
        if not self._connected:
            self.connect()
        return self._connected and self._client is not None

    def read_secret(self, path: str) -> dict | None:
        if not self._ensure():
            return None
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self._mount
            )
            return resp["data"]["data"]
        except Exception as e:
            logger.warning("vault_read_failed", path=path, error=str(e))
            return None

    def write_secret(self, path: str, data: dict) -> bool:
        if not self._ensure():
            return False
        try:
            self._client.secrets.kv.v2.create_or_update_secret(
                path=path, secret=data, mount_point=self._mount
            )
            return True
        except Exception as e:
            logger.warning("vault_write_failed", path=path, error=str(e))
            return False

    def delete_secret(self, path: str) -> bool:
        if not self._ensure():
            return False
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=path, mount_point=self._mount
            )
            return True
        except Exception as e:
            logger.warning("vault_delete_failed", path=path, error=str(e))
            return False

    async def get_platform_refresh_token(self, platform: str, tenant: str) -> str | None:
        path = f"platforms/{platform}/{tenant}"
        data = self.read_secret(path)
        if data and data.get("refresh_token"):
            return data["refresh_token"]
        return await self._fallback_mysql_refresh_token(platform, tenant)

    async def _fallback_mysql_refresh_token(self, platform: str, tenant: str) -> str | None:
        try:
            from db_clients.mysql import get_mysql_client

            mysql = get_mysql_client()
            row = await mysql.fetchone(
                "SELECT token_encrypted FROM platform_authorizations "
                "WHERE platform=%s AND tenant_id=%s AND status='active' LIMIT 1",
                (platform, tenant),
            )
            return row["token_encrypted"] if row else None
        except Exception as e:
            logger.warning("vault_mysql_fallback_failed", error=str(e))
            return None

    async def store_platform_refresh_token(
        self, platform: str, tenant: str, token: str, extra: dict | None = None
    ) -> bool:
        path = f"platforms/{platform}/{tenant}"
        data: dict[str, Any] = {"refresh_token": token}
        if extra:
            data.update(extra)
        return self.write_secret(path, data)

    def get_model_credential(self, adapter_id: str) -> dict | None:
        path = f"models/{adapter_id}"
        data = self.read_secret(path)
        if data:
            return data
        return self._fallback_env_credential(adapter_id)

    def _fallback_env_credential(self, adapter_id: str) -> dict | None:
        env_key = _ENV_MAP.get(adapter_id)
        if not env_key:
            return None
        val = config_manager.get(env_key, "")
        if not val:
            return None
        return {"api_key": val}


vault_client = VaultClient()
