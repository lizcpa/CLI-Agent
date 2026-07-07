from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_worker_publishers_singleton_has_generic():
    from project.backend.publish_dispatcher.worker_publishers import worker_publishers
    from platform_connectors.generic_http_publisher import GenericHTTPPublisher

    pub = worker_publishers.get_publisher("youtube")
    assert isinstance(pub, GenericHTTPPublisher)
    assert "tiktok" in worker_publishers.list_platforms()
    assert "instagram" in worker_publishers.list_platforms()


@pytest.mark.asyncio
async def test_generic_http_publisher_publish_calls_upload_and_publish():
    from platform_connectors.generic_http_publisher import GenericHTTPPublisher
    from platform_connectors.models import (
        PlatformAdapterConfig, PublishRequest, PublishContent,
    )

    cfg = PlatformAdapterConfig(
        platform_id="youtube",
        connector_class="GenericHTTPPublisher",
        config={
            "api_upload_url": "http://up.example",
            "api_publish_url": "http://pub.example",
            "tenant_id": "default",
        },
    )
    pub = GenericHTTPPublisher(cfg)

    mock_upload_resp = MagicMock()
    mock_upload_resp.json.return_value = {"video_id": "vid123"}
    mock_upload_resp.raise_for_status = MagicMock()

    mock_publish_resp = MagicMock()
    mock_publish_resp.json.return_value = {"post_id": "post456", "public_url": "http://x"}
    mock_publish_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[mock_upload_resp, mock_publish_resp])

    req = PublishRequest(
        platform="youtube",
        content=PublishContent(video_url="http://v", title="t", description="d", tags=[]),
    )

    with patch("platform_connectors.generic_http_publisher.httpx.AsyncClient", return_value=mock_client), \
         patch.object(GenericHTTPPublisher, "get_oauth_token", new=AsyncMock(return_value="token")):
        result = await pub.publish(req)

    assert result.platform_post_id == "post456"
    assert result.public_url == "http://x"
    assert mock_client.post.call_count == 2


def test_publish_to_platform_task_inserts_publish_log():
    from project.backend.publish_dispatcher import tasks
    from mq_clients.celery_app import BaseTask
    from platform_connectors.models import PublishResult

    mock_redis = MagicMock()
    safety_ok = MagicMock()
    safety_ok.passed = True

    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock(return_value=PublishResult(
        platform_post_id="post1", public_url="http://pub", status="published",
    ))

    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(return_value={"id": 42})

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks.content_safety_client, "check_video_async", new=AsyncMock(return_value=safety_ok)), \
         patch.object(tasks, "_adapt_video", return_value=None), \
         patch.object(tasks.worker_publishers, "get_publisher", return_value=mock_publisher), \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql):
        mock_rc.return_value = mock_redis
        result = tasks.publish_to_platform_task.run(
            task_id="t1", pipeline_id="p1", platform="youtube",
            video_url="http://v", title="t", description="d",
            tags=["tag"], scheduled_time=None, tenant_id="default",
        )

    assert result["platform_post_id"] == "post1"
    assert result["publish_log_id"] == 42
    execute_calls = mock_mysql.execute.call_args_list
    assert any("INSERT INTO publish_log" in str(c) for c in execute_calls)
    assert any("UPDATE generation_pipelines" in str(c) for c in execute_calls)


def test_publish_to_platform_task_handles_content_filtered():
    from project.backend.publish_dispatcher import tasks
    from mq_clients.celery_app import BaseTask
    from common_sdk.exceptions import ContentFilteredException

    mock_redis = MagicMock()
    safety_fail = MagicMock()
    safety_fail.passed = False
    safety_fail.detail = "racy content"

    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks.content_safety_client, "check_video_async", new=AsyncMock(return_value=safety_fail)), \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql):
        mock_rc.return_value = mock_redis
        with pytest.raises(ContentFilteredException):
            tasks.publish_to_platform_task.run(
                task_id="t1", pipeline_id="p1", platform="youtube",
                video_url="http://v", title="t", description="d",
                tags=[], scheduled_time=None, tenant_id="default",
            )

    execute_calls = mock_mysql.execute.call_args_list
    assert any("INSERT INTO publish_log" in str(c) and "failed" in str(c) for c in execute_calls)


def test_publish_to_platform_task_fails_log_inserted():
    from project.backend.publish_dispatcher import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    safety_ok = MagicMock()
    safety_ok.passed = True

    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock(side_effect=RuntimeError("platform API down"))

    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks.content_safety_client, "check_video_async", new=AsyncMock(return_value=safety_ok)), \
         patch.object(tasks, "_adapt_video", return_value=None), \
         patch.object(tasks.worker_publishers, "get_publisher", return_value=mock_publisher), \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql):
        mock_rc.return_value = mock_redis
        with pytest.raises(RuntimeError):
            tasks.publish_to_platform_task.run(
                task_id="t1", pipeline_id="p1", platform="youtube",
                video_url="http://v", title="t", description="d",
                tags=[], scheduled_time=None, tenant_id="default",
            )

    execute_calls = mock_mysql.execute.call_args_list
    assert any("INSERT INTO publish_log" in str(c) and "failed" in str(c) for c in execute_calls)
