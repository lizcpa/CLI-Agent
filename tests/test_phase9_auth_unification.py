"""Phase 9 Part 1: Auth unification tests.

Verifies that:
- verify_internal_jwt correctly sets tenant_id and rejects bad tokens
- MCP API key validation does DB-backed hash comparison
- asset_manager.verify_internal_request delegates to verify_internal_jwt
"""
from __future__ import annotations

import sys
import time
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import jwt
import pytest

from common_sdk.auth import create_service_jwt, verify_internal_jwt
from common_sdk.exceptions import AuthException

SECRET = "dev-jwt-secret-prodvideofactory-2024"


class _StubState:
    pass


class StubRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.state = _StubState()


class TestVerifyInternalJwt:
    @pytest.mark.asyncio
    async def test_verify_internal_jwt_sets_tenant_id(self):
        token = create_service_jwt("ai-generation", SECRET)
        req = StubRequest()
        await verify_internal_jwt(req, authorization=f"Bearer {token}", x_tenant_id="tenant-abc")
        assert req.state.tenant_id == "tenant-abc"
        assert req.state.service_name == "ai-generation"

    @pytest.mark.asyncio
    async def test_verify_internal_jwt_rejects_invalid_token(self):
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization="Bearer tampered-token", x_tenant_id=None)

    @pytest.mark.asyncio
    async def test_verify_internal_jwt_rejects_missing_header(self):
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization=None, x_tenant_id=None)


class TestMcpApiKeyValidation:
    def test_parse_api_key_rejects_invalid_format(self):
        from project.backend.mcp_gateway.auth import _parse_api_key
        assert _parse_api_key(None) is None
        assert _parse_api_key("") is None
        assert _parse_api_key("invalid") is None
        assert _parse_api_key("Bearer invalid") is None
        assert _parse_api_key("mcp_sk.onlytwo") is None

    def test_parse_api_key_extracts_tenant(self):
        from project.backend.mcp_gateway.auth import _parse_api_key
        result = _parse_api_key("mcp_sk.mytenant.secret123")
        assert result == ("mytenant", "mcp_sk.mytenant.secret123")

    def test_parse_api_key_strips_bearer_prefix(self):
        from project.backend.mcp_gateway.auth import _parse_api_key
        result = _parse_api_key("Bearer mcp_sk.tenant.secret")
        assert result == ("tenant", "mcp_sk.tenant.secret")

    @pytest.mark.asyncio
    async def test_verify_api_key_validates_against_db_hash(self):
        from project.backend.mcp_gateway.auth import verify_api_key

        raw_key = "mcp_sk.testtenant.abc123secret"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        mock_mysql = MagicMock()
        mock_mysql.fetchall = AsyncMock(return_value=[
            {"api_key_hash": key_hash, "scopes": ["read"], "enabled": 1, "expires_at": None}
        ])

        with patch("project.backend.mcp_gateway.auth.get_mysql_client", return_value=mock_mysql):
            result = await verify_api_key(raw_key)
        assert result is not None
        assert result["tenant_id"] == "testtenant"
        assert result["scopes"] == ["read"]

    @pytest.mark.asyncio
    async def test_verify_api_key_rejects_wrong_hash(self):
        from project.backend.mcp_gateway.auth import verify_api_key

        raw_key = "mcp_sk.testtenant.wrongsecret"
        mock_mysql = MagicMock()
        mock_mysql.fetchall = AsyncMock(return_value=[
            {"api_key_hash": "0" * 64, "scopes": None, "enabled": 1, "expires_at": None}
        ])

        with patch("project.backend.mcp_gateway.auth.get_mysql_client", return_value=mock_mysql):
            result = await verify_api_key(raw_key)
        assert result is None


class TestAssetManagerAuthDelegation:
    @pytest.mark.asyncio
    async def test_verify_internal_request_sets_tenant_id(self):
        from project.backend.asset_manager.auth import verify_internal_request

        token = create_service_jwt("asset-manager", SECRET)
        req = StubRequest()
        await verify_internal_request(req, authorization=f"Bearer {token}", x_tenant_id="am-tenant")
        assert req.state.tenant_id == "am-tenant"
        assert req.state.service_name == "asset-manager"

    def test_verify_internal_request_is_verify_internal_jwt(self):
        from project.backend.asset_manager.auth import verify_internal_request
        assert verify_internal_request is verify_internal_jwt
