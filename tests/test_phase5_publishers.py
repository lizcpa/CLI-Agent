from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_base_publisher_refresh_token_if_needed():
    from platform_connectors.base_publisher import BasePlatformPublisher
    from platform_connectors.models import PlatformAdapterConfig
    from platform_connectors.oauth import OAuthFlow

    class TestPublisher(BasePlatformPublisher):
        async def publish(self, request):
            pass

    cfg = {
        "tenant_id": "default",
        "oauth_client_id": "ck",
        "oauth_client_secret": "cs",
        "oauth_refresh_url": "https://example.com/refresh",
        "oauth_token_path": "$.access_token",
        "oauth_refresh_token_path": "$.refresh_token",
        "oauth_open_id_path": "$.open_id",
        "oauth_expires_path": "$.expires_in",
    }
    adapter_cfg = PlatformAdapterConfig(
        platform_id="douyin", connector_class="TestPublisher", config=cfg
    )
    pub = TestPublisher(adapter_cfg)

    mock_vault = MagicMock()
    mock_vault.get_platform_refresh_token = AsyncMock(return_value="old_rt")
    mock_vault.store_platform_refresh_token = AsyncMock(return_value=True)

    refresh_result = {
        "access_token": "at_new",
        "refresh_token": "rt_new",
        "open_id": "oid",
        "expires_in": 7200,
        "raw": {},
    }

    with patch("common_sdk.vault_client.vault_client", mock_vault), patch.object(
        OAuthFlow, "refresh", new=AsyncMock(return_value=refresh_result)
    ):
        asyncio.run(pub.refresh_token_if_needed())

    assert pub._access_token == "at_new"
    assert pub._open_id == "oid"
    assert pub._token_expires_at > time.time()
    mock_vault.get_platform_refresh_token.assert_called_once_with("douyin", "default")
    mock_vault.store_platform_refresh_token.assert_called_once()
    store_args = mock_vault.store_platform_refresh_token.call_args
    assert store_args.args[0] == "douyin"
    assert store_args.args[1] == "default"
    assert store_args.args[2] == "rt_new"


def test_base_publisher_get_oauth_token_fallback_env(monkeypatch):
    from platform_connectors.base_publisher import BasePlatformPublisher
    from platform_connectors.models import PlatformAdapterConfig

    class TestPublisher(BasePlatformPublisher):
        async def publish(self, request):
            pass

    adapter_cfg = PlatformAdapterConfig(
        platform_id="douyin", connector_class="TestPublisher", config={"tenant_id": "default"}
    )
    pub = TestPublisher(adapter_cfg)

    mock_vault = MagicMock()
    mock_vault.get_platform_refresh_token = AsyncMock(return_value=None)
    monkeypatch.setenv("DOUYIN_ACCESS_TOKEN", "env_tok")

    with patch("common_sdk.vault_client.vault_client", mock_vault):
        token = asyncio.run(pub.get_oauth_token())

    assert token == "env_tok"


def test_douyin_publisher_upload_and_post():
    from platform_connectors.douyin_publisher import DouyinPublisher
    from platform_connectors.models import PlatformAdapterConfig, PublishContent

    cfg = {
        "api_upload_url": "https://api.douyin.com/upload",
        "api_publish_url": "https://api.douyin.com/publish",
        "api_video_id_path": "$.data.video.video_id",
        "api_post_id_path": "$.data.item_id",
        "tenant_id": "default",
    }
    adapter_cfg = PlatformAdapterConfig(
        platform_id="douyin", connector_class="DouyinPublisher", config=cfg
    )
    pub = DouyinPublisher(adapter_cfg)
    # Bypass refresh: set a fresh token + open_id
    pub._access_token = "fake_at"
    pub._token_expires_at = time.time() + 3600
    pub._open_id = "fake_oid"

    video_download_resp = MagicMock()
    video_download_resp.raise_for_status = MagicMock()
    video_download_resp.content = b"video-bytes"

    upload_resp = MagicMock()
    upload_resp.json.return_value = {"data": {"video": {"video_id": "vid1"}}}
    upload_resp.raise_for_status = MagicMock()

    publish_resp = MagicMock()
    publish_resp.json.return_value = {"data": {"item_id": "post1"}}
    publish_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=video_download_resp)
    mock_client.post = AsyncMock(side_effect=[upload_resp, publish_resp])

    with patch(
        "platform_connectors.douyin_publisher.httpx.AsyncClient",
        return_value=mock_client,
    ):
        video_id = asyncio.run(pub.upload_video("http://video/1.mp4"))
        assert video_id == "vid1"

        post_id = asyncio.run(
            pub.create_post(
                "vid1", "", PublishContent(video_url="http://v", title="t", description="d")
            )
        )
        assert post_id == "post1"

    # Verify Bearer header + open_id on the publish (2nd) POST call
    post_calls = mock_client.post.call_args_list
    assert len(post_calls) == 2
    publish_call = post_calls[1]
    body = publish_call.kwargs["json"]
    headers = publish_call.kwargs["headers"]
    assert body["open_id"] == "fake_oid"
    assert body["video_id"] == "vid1"
    assert headers["Authorization"] == "Bearer fake_at"


def test_worker_publishers_registry_has_douyin():
    from platform_connectors.douyin_publisher import DouyinPublisher
    from project.backend.publish_dispatcher.worker_publishers import worker_publishers

    pub = worker_publishers.get_publisher("douyin")
    assert pub is not None
    assert isinstance(pub, DouyinPublisher)

    platforms = worker_publishers.list_platforms()
    assert "douyin" in platforms
    assert "youtube" in platforms


def test_web_backend_callback_exchange_and_store():
    from project.backend.web_backend import routes
    from project.backend.web_backend.routes import platform_oauth_callback

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(
        return_value=json.dumps(
            {"platform": "douyin", "tenant_id": "default", "created_at": 0}
        )
    )
    mock_redis.delete = AsyncMock(return_value=1)

    mock_mysql = MagicMock()
    mock_mysql.fetchall = AsyncMock(
        return_value=[
            {"config_key": "oauth_client_id", "config_value": "ck"},
            {"config_key": "oauth_client_secret", "config_value": "cs"},
            {"config_key": "oauth_token_url", "config_value": "https://token"},
        ]
    )
    mock_mysql.execute = AsyncMock()

    mock_vault = MagicMock()
    mock_vault.store_platform_refresh_token = AsyncMock(return_value=True)

    token_data = {
        "access_token": "at",
        "refresh_token": "rt",
        "open_id": "oid",
        "expires_in": 3600,
        "raw": {},
    }

    mock_request = MagicMock()
    mock_request.state.tenant_id = "default"

    with patch.object(routes, "get_redis_client", return_value=mock_redis), patch.object(
        routes, "get_mysql_client", return_value=mock_mysql
    ), patch.object(routes, "vault_client", mock_vault), patch.object(
        routes.OAuthFlow, "exchange_code", new=AsyncMock(return_value=token_data)
    ):
        result = asyncio.run(
            platform_oauth_callback(
                payload={"platform": "douyin", "code": "c1", "state": "s1"},
                request=mock_request,
            )
        )

    body = json.loads(result.body)
    assert body["data"]["authorized"] is True
    assert body["data"]["open_id"] == "oid"

    mock_vault.store_platform_refresh_token.assert_called_once()
    store_args = mock_vault.store_platform_refresh_token.call_args
    assert store_args.args[0] == "douyin"
    assert store_args.args[2] == "rt"

    mock_mysql.execute.assert_called_once()
    sql = mock_mysql.execute.call_args.args[0]
    assert "INSERT INTO platform_authorizations" in sql
    assert "token_encrypted" in sql
    assert "platform_user_id" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
