from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_mock_resp(data: dict):
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_async_client_mock(post_resp):
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=post_resp)
    return mock_client


def test_oauthflow_build_auth_url():
    from platform_connectors.oauth import OAuthFlow

    cfg = {
        "oauth_client_id": "ck123",
        "oauth_client_secret": "cs456",
        "oauth_redirect_uri": "https://app.example.com/cb",
        "oauth_auth_url": "https://open.douyin.com/platform/oauth/connect/",
        "oauth_scope": "video.create,video.data",
        "oauth_client_id_field": "client_key",
    }
    flow = OAuthFlow("douyin", cfg)
    url = flow.build_auth_url("state123")
    assert "client_key=ck123" in url
    assert "state=state123" in url
    assert "video.create" in url
    assert "redirect_uri=https" in url
    assert "response_type=code" in url
    # default secret field should NOT appear in auth URL (only client_id + scope)
    assert "client_secret" not in url


def test_oauthflow_exchange_code():
    from platform_connectors.oauth import OAuthFlow

    cfg = {
        "oauth_client_id": "ck123",
        "oauth_client_secret": "cs456",
        "oauth_redirect_uri": "https://app.example.com/cb",
        "oauth_token_url": "https://open.douyin.com/oauth/access_token/",
        "oauth_token_path": "$.data.access_token",
        "oauth_refresh_token_path": "$.data.refresh_token",
        "oauth_open_id_path": "$.data.open_id",
        "oauth_expires_path": "$.data.expires_in",
    }
    flow = OAuthFlow("douyin", cfg)
    mock_resp = _make_mock_resp(
        {
            "data": {
                "access_token": "at123",
                "refresh_token": "rt456",
                "open_id": "oid789",
                "expires_in": 3600,
            }
        }
    )
    mock_client = _make_async_client_mock(mock_resp)
    with patch("platform_connectors.oauth.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(flow.exchange_code("code123"))

    assert result["access_token"] == "at123"
    assert result["refresh_token"] == "rt456"
    assert result["open_id"] == "oid789"
    assert result["expires_in"] == 3600

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "access_token" in call_args.args[0]
    body = call_args.kwargs["data"]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "code123"
    assert body["client_id"] == "ck123"


def test_oauthflow_refresh_uses_refresh_url_and_grant_type():
    from platform_connectors.oauth import OAuthFlow

    cfg = {
        "oauth_client_id": "ck123",
        "oauth_client_secret": "cs456",
        "oauth_redirect_uri": "https://app.example.com/cb",
        "oauth_token_url": "https://open.douyin.com/oauth/access_token/",
        "oauth_refresh_url": "https://open.douyin.com/oauth/refresh_token/",
        "oauth_token_path": "$.data.access_token",
        "oauth_refresh_token_path": "$.data.refresh_token",
        "oauth_open_id_path": "$.data.open_id",
        "oauth_expires_path": "$.data.expires_in",
    }
    flow = OAuthFlow("douyin", cfg)
    mock_resp = _make_mock_resp(
        {
            "data": {
                "access_token": "at_new",
                "refresh_token": "rt_new",
                "open_id": "oid_new",
                "expires_in": 7200,
            }
        }
    )
    mock_client = _make_async_client_mock(mock_resp)
    with patch("platform_connectors.oauth.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(flow.refresh("old_rt"))

    assert result["access_token"] == "at_new"
    assert result["refresh_token"] == "rt_new"
    assert result["open_id"] == "oid_new"
    assert result["expires_in"] == 7200

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    # refresh_url must be used (not token_url)
    assert "refresh_token" in call_args.args[0]
    body = call_args.kwargs["data"]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "old_rt"
