from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_route_post(route_map: dict):
    """Build an AsyncMock for InternalHTTPClient.post that routes by URL substring.

    Values that are dicts are returned directly; values that are Exception
    instances are raised.
    """
    async def _post(url, *, json_data=None, target="default", tenant_id=None):
        for key, val in route_map.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise RuntimeError(f"unexpected url: {url}")
    return AsyncMock(side_effect=_post)


def _make_mock_http(route_map: dict):
    mock_http = MagicMock()
    mock_http.post = _make_route_post(route_map)
    mock_http.close = AsyncMock()
    return mock_http


def test_run_pipeline_task_full_dag():
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test Product", "description": "desc", "main_image_url": "http://img", "tags": ["tag1"]},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 1},
        "copywriting": {"text": "营销文案"},
        "images/generate": {"image_objects": ["prodvideofactory/img/1.jpg"]},
        "videos/generate": {"clip_objects": ["prodvideofactory/clip/1.mp4"]},
        "compose": {"output_object": "prodvideofactory/final/1/1/output.mp4"},
        "publish": {"platform_post_id": "post1", "publish_log_id": 42},
    }
    mock_http = _make_mock_http(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "InternalHTTPClient", return_value=mock_http):
        mock_rc.return_value = mock_redis
        result = tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    assert result["pipeline_id"] == 1
    assert "final_video_url" in result
    execute_calls = mock_mysql.execute.call_args_list
    assert any("INSERT INTO generation_pipelines" in str(c) for c in execute_calls)
    assert any("stage" in str(c) and "composing" in str(c) for c in execute_calls)
    assert any("stage" in str(c) and "completed" in str(c) for c in execute_calls)
    mock_http.close.assert_awaited()


def test_run_pipeline_task_handles_generation_partial_failure():
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test", "description": "d", "main_image_url": "", "tags": []},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 0},
        "copywriting": {"text": "文案"},
        "images/generate": RuntimeError("image API down"),
        "videos/generate": {"clip_objects": ["c1.mp4"]},
        "compose": {"output_object": "out.mp4"},
        "publish": {"publish_log_id": 42},
    }
    mock_http = _make_mock_http(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "InternalHTTPClient", return_value=mock_http):
        mock_rc.return_value = mock_redis
        result = tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    assert result["pipeline_id"] == 1
    execute_calls = mock_mysql.execute.call_args_list
    assert any("images_status" in str(c) and "failed" in str(c) for c in execute_calls)


def test_run_pipeline_task_fails_when_no_video_clips():
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test", "description": "d", "main_image_url": "", "tags": []},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 0},
        "copywriting": RuntimeError("copy down"),
        "images/generate": RuntimeError("img down"),
        "videos/generate": {"clip_objects": []},
    }
    mock_http = _make_mock_http(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "InternalHTTPClient", return_value=mock_http):
        mock_rc.return_value = mock_redis
        with pytest.raises(RuntimeError, match="No video clips generated"):
            tasks.run_pipeline_task.run(
                task_id="t1", product_id=100, tenant_id="default", config=None,
            )

    execute_calls = mock_mysql.execute.call_args_list
    assert any("stage" in str(c) and "failed" in str(c) for c in execute_calls)


@pytest.mark.asyncio
async def test_hot_score_subscriber_triggers_pipeline():
    from project.backend.pipeline_orchestrator.subscriber import HotScoreSubscriber

    sub = HotScoreSubscriber()
    mock_celery = MagicMock()
    mock_celery.send_task = MagicMock()

    with patch("mq_clients.celery_app.get_celery_app", return_value=mock_celery):
        await sub._handle_message('{"product_id": 42, "score": 85.5, "tenant_id": "default"}')

    mock_celery.send_task.assert_called_once()
    call_kwargs = mock_celery.send_task.call_args
    assert call_kwargs.kwargs["queue"] == "orchestrator_queue"
    assert call_kwargs.kwargs["args"][0].startswith("pipe_")
    assert call_kwargs.kwargs["args"][1] == 42


@pytest.mark.asyncio
async def test_hot_score_subscriber_ignores_low_score():
    from project.backend.pipeline_orchestrator.subscriber import HotScoreSubscriber

    sub = HotScoreSubscriber()
    mock_celery = MagicMock()

    with patch("mq_clients.celery_app.get_celery_app", return_value=mock_celery):
        await sub._handle_message('{"product_id": 42, "score": 50.0, "tenant_id": "default"}')

    mock_celery.send_task.assert_not_called()


def test_create_pipeline_route():
    from project.backend.pipeline_orchestrator.routes import router, CreatePipelineRequest

    routes = [r.path for r in router.routes]
    assert "/api/v1/pipelines" in routes
    assert "/api/v1/pipelines/{pipeline_id}" in routes
