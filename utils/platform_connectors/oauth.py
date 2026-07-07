from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from .mapper import resolve_jsonpath

logger = logging.getLogger(__name__)


class OAuthFlow:
    """Config-driven OAuth2 authorization code flow + token refresh.

    All platform-specific variability (endpoints, scopes, JsonPath response paths)
    is passed via the ``cfg`` dict, typically loaded from the ``platform_config`` table.
    """

    def __init__(self, platform: str, cfg: dict[str, str]) -> None:
        self.platform = platform
        self.client_id = cfg.get("oauth_client_id", "")
        self.client_secret = cfg.get("oauth_client_secret", "")
        self.redirect_uri = cfg.get("oauth_redirect_uri", "")
        self.auth_url = cfg.get("oauth_auth_url", "")
        self.token_url = cfg.get("oauth_token_url", "")
        self.refresh_url = cfg.get("oauth_refresh_url", self.token_url)
        self.scope = cfg.get("oauth_scope", "")
        self.id_field = cfg.get("oauth_client_id_field", "client_id")
        self.secret_field = cfg.get("oauth_client_secret_field", "client_secret")
        self.token_path = cfg.get("oauth_token_path", "$.access_token")
        self.refresh_token_path = cfg.get("oauth_refresh_token_path", "$.refresh_token")
        self.open_id_path = cfg.get("oauth_open_id_path", "")
        self.expires_path = cfg.get("oauth_expires_path", "$.expires_in")

    def build_auth_url(self, state: str) -> str:
        params = {
            self.id_field: self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if self.scope:
            params["scope"] = self.scope
        return f"{self.auth_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for tokens. Returns normalized dict."""
        body = {
            self.id_field: self.client_id,
            self.secret_field: self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.token_url, data=body)
            resp.raise_for_status()
            data = resp.json()
        return self._normalize(data)

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Refresh access token using a refresh token. Returns normalized dict."""
        body = {
            self.id_field: self.client_id,
            self.secret_field: self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.refresh_url, data=body)
            resp.raise_for_status()
            data = resp.json()
        return self._normalize(data)

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        access_token = resolve_jsonpath(data, self.token_path) or ""
        refresh_token = resolve_jsonpath(data, self.refresh_token_path) or ""
        open_id = resolve_jsonpath(data, self.open_id_path) if self.open_id_path else ""
        expires_in = resolve_jsonpath(data, self.expires_path)
        try:
            expires_in = int(expires_in) if expires_in else 3600
        except (TypeError, ValueError):
            expires_in = 3600
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "open_id": open_id or "",
            "raw": data,
        }
