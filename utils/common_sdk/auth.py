from __future__ import annotations

import hashlib
import secrets
import time

import jwt
from fastapi import Header, Request

from .config import config_manager
from .exceptions import AuthException


def create_service_jwt(service_name: str, secret: str) -> str:
    now = int(time.time())
    payload: dict[str, str | int] = {
        "sub": service_name,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_service_jwt(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"])


def create_api_key(tenant_id: str, scopes: list[str] | None = None) -> tuple[str, str]:
    random_hex = secrets.token_hex(32)
    api_key = f"mcp_sk.{tenant_id}.{random_hex}"
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    return api_key, key_hash


def verify_api_key(key: str, key_hash: str) -> bool:
    return hashlib.sha256(key.encode()).hexdigest() == key_hash


def get_tenant_id_from_api_key(key: str) -> str:
    parts = key.split(".")
    if len(parts) >= 3 and parts[0] == "mcp_sk":
        return parts[1]
    raise ValueError("Invalid API key format")


async def verify_internal_jwt(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> dict:
    if not authorization:
        raise AuthException(message="Missing Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthException(message="Empty JWT token")

    secret = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
    try:
        payload = decode_service_jwt(token, secret)
    except jwt.ExpiredSignatureError:
        raise AuthException(message="JWT token expired")
    except jwt.InvalidTokenError:
        raise AuthException(message="Invalid JWT token")

    request.state.tenant_id = x_tenant_id or payload.get("tenant_id", "default")
    request.state.service_name = payload.get("sub", "unknown")
    return payload
