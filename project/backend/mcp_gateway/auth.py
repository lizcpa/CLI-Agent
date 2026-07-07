from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException, Request

from utils.common_sdk.config import config_manager
from utils.db_clients.mysql import get_mysql_client

logger = logging.getLogger(__name__)


def _parse_api_key(raw: str | None) -> tuple[str, str] | None:
    """Extract (tenant_id, full_key) from a raw Authorization header value.

    Accepts both ``Bearer mcp_sk.<tenant>.<secret>`` and bare
    ``mcp_sk.<tenant>.<secret>``.  Returns ``None`` on malformed input.
    """
    if not raw:
        return None
    token = raw.removeprefix("Bearer ").strip()
    if not token.startswith("mcp_sk."):
        return None
    parts = token.split(".")
    if len(parts) < 3 or parts[0] != "mcp_sk":
        return None
    return parts[1], token


def get_tenant_id_from_key(api_key: str) -> str:
    parts = api_key.split(".")
    if len(parts) >= 2 and parts[0] == "mcp_sk":
        return parts[1]
    return "unknown"


async def verify_api_key(api_key: str | None) -> dict | None:
    """Validate an MCP API key against the ``api_keys`` table.

    Performs a SHA-256 hash comparison so the database never stores raw
    keys.  Returns ``{"tenant_id", "scopes", "api_key_prefix"}`` on success
    or ``None`` on failure.
    """
    parsed = _parse_api_key(api_key)
    if not parsed:
        logger.warning("Missing or invalid API key format")
        return None

    tenant_id, token = parsed
    key_hash = hashlib.sha256(token.encode()).hexdigest()

    try:
        mysql = get_mysql_client()
        rows = await mysql.fetchall(
            "SELECT api_key_hash, scopes, enabled, expires_at FROM api_keys "
            "WHERE tenant_id = %s AND enabled = 1",
            (tenant_id,),
        )
    except Exception as e:
        logger.exception("DB error during API key verification: %s", e)
        return None

    for row in rows:
        if row["api_key_hash"] == key_hash:
            if row.get("expires_at") is not None:
                from datetime import datetime
                if datetime.now() > row["expires_at"]:
                    logger.warning("Expired API key for tenant %s", tenant_id)
                    return None
            return {
                "tenant_id": tenant_id,
                "scopes": row.get("scopes"),
                "api_key_prefix": token[:30],
            }
    logger.warning("No matching API key hash for tenant %s", tenant_id)
    return None


async def verify_mcp_api_key(request: Request) -> dict:
    """FastAPI dependency that authenticates MCP requests via API key.

    On success, sets ``request.state.tenant_id`` and
    ``request.state.scopes`` and returns the auth payload.  On failure,
    raises HTTP 401.  In development, ``MCP_AUTH_DISABLED=true`` bypasses
    validation (useful when the ``api_keys`` table is empty).
    """
    if config_manager.get_bool("MCP_AUTH_DISABLED", False):
        request.state.tenant_id = "default"
        request.state.scopes = None
        return {"tenant_id": "default", "scopes": None, "api_key_prefix": "dev-bypass"}

    auth_header = request.headers.get("Authorization", "")
    payload = await verify_api_key(auth_header)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    request.state.tenant_id = payload["tenant_id"]
    request.state.scopes = payload.get("scopes")
    return payload
