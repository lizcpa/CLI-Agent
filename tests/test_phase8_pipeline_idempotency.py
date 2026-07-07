"""Phase 8 tests: pipeline idempotency guard + cleanup."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from common_sdk.exceptions import ValidationException


async def test_duplicate_pipeline_request_rejected():
    """Second SET NX returns None → ValidationException raised."""
    from project.backend.pipeline_orchestrator.routes import (
        create_pipeline,
        CreatePipelineRequest,
    )

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(side_effect=[True, None])
    mock_redis.get = AsyncMock(return_value="existing_task_id")

    body = CreatePipelineRequest(product_id=100)
    req = MagicMock()
    req.state.tenant_id = "default"

    with patch(
        "project.backend.pipeline_orchestrator.routes._get_redis",
        return_value=mock_redis,
    ), patch("mq_clients.celery_app.get_celery_app"):
        result = await create_pipeline(req, body)
        assert result["status"] == "queued"

        with pytest.raises(ValidationException) as exc_info:
            await create_pipeline(req, body)
        assert "already active" in exc_info.value.message
        assert exc_info.value.data["existing_task_id"] == "existing_task_id"


def test_idempotency_key_deleted_on_task_completion():
    """tasks.py finally block deletes pipeline:active:{product_id}:{tenant_id}."""
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
        "analyze": {"analyzed_count": 1, "hot_count": 1},
        "copywriting": {"text": "文案"},
        "images/generate": {"image_objects": ["img1.jpg"]},
        "videos/generate": {"clip_objects": ["c1.mp4"]},
        "compose": {"output_object": "out.mp4"},
        "publish": {"publish_log_id": 42},
    }
    from tests.test_pipeline_orchestrator import _make_mock_http
    mock_http = _make_mock_http(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "InternalHTTPClient", return_value=mock_http):
        mock_rc.return_value = mock_redis
        tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    mock_redis.delete.assert_any_call("pipeline:active:100:default")
