from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import jwt
import pytest

from common_sdk.auth import create_service_jwt, verify_internal_jwt
from common_sdk.exceptions import AuthException

SECRET = "dev-jwt-secret-prodvideofactory-2024"


class _StubState:
    pass


class StubRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.state = _StubState()


class TestVerifyInternalJwt:
    def _make_token(self, sub: str = "ai-generation", expired: bool = False) -> str:
        now = int(time.time())
        if expired:
            payload = {"sub": sub, "iat": now - 7200, "exp": now - 3600}
        else:
            payload = {"sub": sub, "iat": now, "exp": now + 3600, "tenant_id": "payload-tenant"}
        return jwt.encode(payload, SECRET, algorithm="HS256")

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_payload_and_injects_state(self):
        token = self._make_token("ai-generation")
        req = StubRequest()
        payload = await verify_internal_jwt(
            req, authorization=f"Bearer {token}", x_tenant_id="tenant-1"
        )
        assert payload["sub"] == "ai-generation"
        assert req.state.tenant_id == "tenant-1"
        assert req.state.service_name == "ai-generation"

    @pytest.mark.asyncio
    async def test_tenant_falls_back_to_payload_when_header_missing(self):
        token = self._make_token("ai-generation")
        req = StubRequest()
        payload = await verify_internal_jwt(
            req, authorization=f"Bearer {token}", x_tenant_id=None
        )
        assert req.state.tenant_id == "payload-tenant"
        assert req.state.service_name == "ai-generation"

    @pytest.mark.asyncio
    async def test_tenant_falls_back_to_default_when_neither_present(self):
        token = create_service_jwt("product-analyzer", SECRET)
        req = StubRequest()
        await verify_internal_jwt(req, authorization=f"Bearer {token}", x_tenant_id=None)
        assert req.state.tenant_id == "default"
        assert req.state.service_name == "product-analyzer"

    @pytest.mark.asyncio
    async def test_missing_authorization_raises(self):
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization=None, x_tenant_id=None)

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization="Bearer ", x_tenant_id=None)

    @pytest.mark.asyncio
    async def test_expired_jwt_raises(self):
        token = self._make_token(expired=True)
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization=f"Bearer {token}", x_tenant_id=None)

    @pytest.mark.asyncio
    async def test_tampered_jwt_raises(self):
        token = self._make_token()
        tampered = token[:-6] + "XXXXXX"
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization=f"Bearer {tampered}", x_tenant_id=None)

    @pytest.mark.asyncio
    async def test_wrong_secret_jwt_raises(self):
        payload = {"sub": "evil", "iat": int(time.time()), "exp": int(time.time()) + 3600}
        token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        req = StubRequest()
        with pytest.raises(AuthException):
            await verify_internal_jwt(req, authorization=f"Bearer {token}", x_tenant_id=None)

    def test_service_auth_reexports_verify_internal_jwt(self):
        from project.backend.product_analyzer.auth import verify_internal_jwt as pa_auth
        from project.backend.crawl_scheduler.auth import verify_internal_jwt as cs_auth
        from project.backend.asset_manager.auth import verify_internal_request as am_auth

        from common_sdk.auth import verify_internal_jwt as common_auth

        assert pa_auth is common_auth
        assert cs_auth is common_auth
        assert am_auth is common_auth
