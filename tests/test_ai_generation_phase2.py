from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_get_router_service_returns_singleton():
    from project.backend.ai_generation import main
    from project.backend.ai_generation.routes import get_router_service
    from project.backend.ai_generation.router import ModelRouterService

    fake = MagicMock(spec=ModelRouterService)
    main._router_service = fake
    try:
        assert get_router_service() is fake
    finally:
        main._router_service = None


def test_get_router_service_raises_when_not_initialized():
    from project.backend.ai_generation import main
    from project.backend.ai_generation.routes import get_router_service
    from common_sdk.exceptions import ServiceException

    main._router_service = None
    with pytest.raises(ServiceException):
        get_router_service()


@pytest.mark.asyncio
async def test_routes_internal_llm_chat_uses_async():
    from project.backend.ai_generation.routes import internal_llm_chat

    mock_adapter = MagicMock()
    mock_adapter.adapter_id = "openai_gpt4o"
    mock_adapter.chat_async = AsyncMock(return_value={"text": "hello"})

    mock_router_svc = MagicMock()
    mock_router_svc.route_llm.return_value = mock_adapter
    mock_router_svc.router = MagicMock()

    await internal_llm_chat(
        request={"messages": [{"role": "user", "content": "hi"}], "model": "gpt-4o"},
        _payload={"sub": "test"},
        router_svc=mock_router_svc,
    )

    mock_adapter.chat_async.assert_awaited_once()
    mock_router_svc.route_llm.assert_called_once()


@pytest.mark.asyncio
async def test_routes_internal_image_generate_uses_async():
    from project.backend.ai_generation.routes import internal_image_generate

    mock_adapter = MagicMock()
    mock_adapter.adapter_id = "dalle3"
    mock_adapter.generate_async = AsyncMock(return_value={"image_objects": ["bucket/obj"]})

    mock_router_svc = MagicMock()
    mock_router_svc.route_image.return_value = mock_adapter
    mock_router_svc.router = MagicMock()

    await internal_image_generate(
        request={"prompts": ["a cat"], "n": 1, "tenant_id": "t1"},
        _payload={"sub": "test"},
        router_svc=mock_router_svc,
    )

    mock_adapter.generate_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_routes_internal_video_generate_uses_async():
    from project.backend.ai_generation.routes import internal_video_generate

    mock_adapter = MagicMock()
    mock_adapter.adapter_id = "veo3"
    mock_adapter.generate_async = AsyncMock(return_value={"clip_objects": ["bucket/clip"]})

    mock_router_svc = MagicMock()
    mock_router_svc.route_video.return_value = mock_adapter
    mock_router_svc.router = MagicMock()

    await internal_video_generate(
        request={"type": "short", "prompts": ["a dog"], "tenant_id": "t1"},
        _payload={"sub": "test"},
        router_svc=mock_router_svc,
    )

    mock_adapter.generate_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_routes_internal_tts_synthesize_uses_async():
    from project.backend.ai_generation.routes import internal_tts_synthesize

    mock_adapter = MagicMock()
    mock_adapter.adapter_id = "azure_tts"
    mock_adapter.synthesize_async = AsyncMock(return_value={"audio_object": "bucket/audio"})

    mock_router_svc = MagicMock()
    mock_router_svc.route_tts.return_value = mock_adapter
    mock_router_svc.router = MagicMock()

    await internal_tts_synthesize(
        request={"text": "hello", "tenant_id": "t1"},
        _payload={"sub": "test"},
        router_svc=mock_router_svc,
    )

    mock_adapter.synthesize_async.assert_awaited_once()


def test_worker_router_singleton_exists():
    from project.backend.ai_generation.worker_router import worker_router
    from project.backend.ai_generation.router import ModelRouterService

    assert isinstance(worker_router, ModelRouterService)


def test_tasks_generate_images_returns_image_objects():
    from project.backend.ai_generation import tasks
    from mq_clients.celery_app import BaseTask

    mock_adapter = MagicMock()
    mock_adapter.generate_async = AsyncMock(return_value={"image_objects": ["prodvideofactory/generated/img1"]})

    mock_redis = MagicMock()
    safety_ok = MagicMock()
    safety_ok.passed = True

    with patch.object(tasks.worker_router, "route_image", return_value=mock_adapter), \
         patch.object(tasks, "presigned_url", return_value="http://mock-url"), \
         patch.object(tasks.content_safety_client, "check_image", return_value=safety_ok), \
         patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc:
        mock_rc.return_value = mock_redis
        result = tasks.generate_images_task.run(
            task_id="test-task",
            prompts=["a cat"],
            size="1024x1024",
            n=1,
            tenant_id="default",
            pipeline_id="",
        )

    assert "image_objects" in result
    assert "storage.prodvideo.local" not in str(result)


@pytest.mark.asyncio
async def test_cost_calculator_log_usage_async_writes_mysql():
    from model_adapters.cost import CostCalculator
    from model_adapters.base import UsageRecord

    calc = CostCalculator()
    record = UsageRecord(
        adapter_id="openai_gpt4o",
        adapter_type="llm",
        model="gpt-4o",
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.001,
    )

    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()

    with patch("db_clients.mysql.get_mysql_client", return_value=mock_mysql):
        await calc.log_usage_async(record)

    mock_mysql.execute.assert_awaited_once()
    call_args = mock_mysql.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO model_usage_log" in sql


def test_cost_calculator_log_usage_deprecated_warns():
    from model_adapters.cost import CostCalculator
    from model_adapters.base import UsageRecord

    calc = CostCalculator()
    record = UsageRecord(adapter_id="test", adapter_type="llm", model="gpt-4o")

    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()

    with patch("db_clients.mysql.get_mysql_client", return_value=mock_mysql):
        with pytest.warns(DeprecationWarning):
            calc.log_usage(record)
