import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest
from common_sdk import (
    APIResponse, success_response, error_response, async_task_response, paginated_response,
    AppException, NotFoundException, ValidationException, AuthException, ForbiddenException, ServiceException, app_exception_handler,
    create_service_jwt, decode_service_jwt, create_api_key, verify_api_key, get_tenant_id_from_api_key,
    ConfigManager, get_logger,
)


class TestResponses:
    def test_success_response(self):
        r = success_response({"id": 1, "name": "test"})
        assert r.code == 0
        assert r.message == "success"
        assert r.data == {"id": 1, "name": "test"}

    def test_success_response_none_data(self):
        r = success_response(None)
        assert r.code == 0
        assert r.data is None

    def test_error_response(self):
        r = error_response(400, "bad request")
        assert r.code == 400
        assert r.message == "bad request"
        assert r.data is None

    def test_async_task_response(self):
        r = async_task_response("uuid-123", 30)
        assert r.code == 0
        assert r.data["task_id"] == "uuid-123"
        assert r.data["status"] == "queued"
        assert r.data["estimated_seconds"] == 30

    def test_paginated_response(self):
        r = paginated_response([1, 2, 3], 100, 2, 10)
        assert r.code == 0
        assert r.data["items"] == [1, 2, 3]
        assert r.data["total"] == 100
        assert r.data["page"] == 2
        assert r.data["page_size"] == 10

    def test_response_serialization(self):
        r = error_response(500, "server error")
        d = r.model_dump()
        assert d["code"] == 500
        assert d["message"] == "server error"


class TestExceptions:
    def test_not_found(self):
        e = NotFoundException("resource not found")
        assert e.code == 404
        assert e.http_status == 404
        assert e.message == "resource not found"

    def test_validation(self):
        e = ValidationException("invalid input")
        assert e.code == 422
        assert e.http_status == 422

    def test_auth(self):
        e = AuthException("unauthorized")
        assert e.code == 401
        assert e.http_status == 401

    def test_forbidden(self):
        e = ForbiddenException("access denied")
        assert e.code == 403

    def test_service_exception(self):
        e = ServiceException("internal error")
        assert e.code == 500
        assert e.http_status == 500

    def test_inheritance(self):
        e = NotFoundException("test")
        assert isinstance(e, AppException)
        assert isinstance(e, Exception)

    def test_exception_to_dict(self):
        e = ValidationException("test message")
        assert str(e) == "test message"


class TestAuth:
    def test_jwt_create_decode(self):
        secret = "test-secret-20240630-long-enough-for-hs256-minimum-32-bytes"
        token = create_service_jwt("test-service", secret)
        claims = decode_service_jwt(token, secret)
        assert claims["sub"] == "test-service"
        assert "iat" in claims
        assert "exp" in claims
        assert claims["exp"] > claims["iat"]

    def test_jwt_invalid_token(self):
        secret = "test-secret-20240630-long-enough-for-hs256-minimum-32-bytes"
        with pytest.raises(Exception):
            decode_service_jwt("invalid.token.here", secret)

    def test_jwt_wrong_secret(self):
        secret_a = "test-secret-20240630-long-enough-for-hs256-A"
        secret_b = "test-secret-20240630-long-enough-for-hs256-B"
        token = create_service_jwt("test", secret_a)
        with pytest.raises(Exception):
            decode_service_jwt(token, secret_b)

    def test_api_key_create_verify(self):
        key, key_hash = create_api_key("tenant_001")
        assert key.startswith("mcp_sk.tenant_001.")
        assert len(key) > 40
        assert verify_api_key(key, key_hash) is True

    def test_api_key_wrong_key(self):
        _, key_hash = create_api_key("tenant_001")
        assert verify_api_key("wrong_key", key_hash) is False

    def test_get_tenant_from_key(self):
        key, _ = create_api_key("my-tenant-123")
        assert get_tenant_id_from_api_key(key) == "my-tenant-123"

    def test_get_tenant_invalid_format(self):
        with pytest.raises(ValueError):
            get_tenant_id_from_api_key("invalid_key")


class TestConfig:
    def test_config_manager_env(self):
        import os
        os.environ["TEST_CONFIG_KEY"] = "test_value"
        cfg = ConfigManager()
        assert cfg.get("TEST_CONFIG_KEY") == "test_value"

    def test_config_manager_default(self):
        cfg = ConfigManager()
        assert cfg.get("NONEXISTENT_KEY", "fallback") == "fallback"

    def test_config_manager_int(self):
        import os
        os.environ["TEST_INT_KEY"] = "42"
        cfg = ConfigManager()
        assert cfg.get_int("TEST_INT_KEY") == 42

    def test_config_manager_bool(self):
        import os
        os.environ["TEST_BOOL_KEY"] = "true"
        cfg = ConfigManager()
        assert cfg.get_bool("TEST_BOOL_KEY") is True
        os.environ["TEST_BOOL_KEY"] = "false"
        assert cfg.get_bool("TEST_BOOL_KEY") is False
