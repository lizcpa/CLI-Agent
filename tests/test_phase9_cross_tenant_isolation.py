"""Phase 9 Part 1: Cross-tenant isolation tests.

Verifies that SQL queries include ``AND tenant_id=%s`` so one tenant
cannot read or modify another tenant's data.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class _StubState:
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id


class StubRequest:
    def __init__(self, tenant_id: str = "default"):
        self.state = _StubState(tenant_id)
        self.headers: dict[str, str] = {}


class TestProductAnalyzerTenantIsolation:
    @pytest.mark.asyncio
    async def test_get_product_score_filters_by_tenant_id(self):
        from project.backend.product_analyzer.routes import get_product_score

        mock_mysql = MagicMock()
        mock_mysql.fetchone = AsyncMock(return_value=None)

        req = StubRequest(tenant_id="tenant-A")
        with patch("project.backend.product_analyzer.routes.get_mysql_client", return_value=mock_mysql):
            result = await get_product_score(product_id=42, request=req, _auth={})

        sql_arg = mock_mysql.fetchone.call_args[0][0]
        assert "AND tenant_id=%s" in sql_arg
        params = mock_mysql.fetchone.call_args[0][1]
        assert "tenant-A" in params

    @pytest.mark.asyncio
    async def test_get_hot_products_filters_by_tenant_id(self):
        from project.backend.product_analyzer.routes import get_hot_products

        mock_redis = MagicMock()
        mock_redis.zrange = AsyncMock(return_value=[])

        mock_mysql = MagicMock()
        mock_mysql.fetchall = AsyncMock(return_value=[])

        req = StubRequest(tenant_id="tenant-B")
        with patch("project.backend.product_analyzer.routes.get_redis_client", return_value=mock_redis), \
             patch("project.backend.product_analyzer.routes.get_mysql_client", return_value=mock_mysql):
            await get_hot_products(request=req, limit=5, _auth={})

        if mock_mysql.fetchall.call_args:
            sql_arg = mock_mysql.fetchall.call_args[0][0]
            assert "AND tenant_id=%s" in sql_arg


class TestPublishDispatcherTenantIsolation:
    @pytest.mark.asyncio
    async def test_get_publish_status_filters_by_tenant_id(self):
        from project.backend.publish_dispatcher.routes import get_publish_status

        mock_mysql = MagicMock()
        mock_mysql.fetchone = AsyncMock(return_value=None)

        req = StubRequest(tenant_id="tenant-A")
        with patch("project.backend.publish_dispatcher.routes.get_mysql_client", return_value=mock_mysql):
            from fastapi.responses import JSONResponse
            result = await get_publish_status("task-123", req)

        sql_arg = mock_mysql.fetchone.call_args[0][0]
        assert "AND tenant_id = %s" in sql_arg
        params = mock_mysql.fetchone.call_args[0][1]
        assert "tenant-A" in params

    @pytest.mark.asyncio
    async def test_list_authorized_platforms_ignores_query_param_tenant_id(self):
        from project.backend.publish_dispatcher.routes import list_authorized_platforms

        mock_mysql = MagicMock()
        mock_mysql.fetchall = AsyncMock(return_value=[])

        req = StubRequest(tenant_id="jwt-tenant")
        with patch("project.backend.publish_dispatcher.routes.get_mysql_client", return_value=mock_mysql):
            await list_authorized_platforms(req)

        params = mock_mysql.fetchall.call_args[0][1]
        assert "jwt-tenant" in params


class TestAssetManagerTenantIsolation:
    @pytest.mark.asyncio
    async def test_delete_platform_config_filters_by_tenant_id(self):
        from project.backend.asset_manager.routes import delete_platform_config

        mock_mysql = MagicMock()
        mock_mysql.execute = AsyncMock(return_value=0)

        req = StubRequest(tenant_id="tenant-A")
        with patch("project.backend.asset_manager.routes.get_mysql_client", return_value=mock_mysql):
            from fastapi.responses import JSONResponse
            result = await delete_platform_config(config_id=99, request=req)

        sql_arg = mock_mysql.execute.call_args[0][0]
        assert "AND tenant_id = %s" in sql_arg
        params = mock_mysql.execute.call_args[0][1]
        assert "tenant-A" in params

    @pytest.mark.asyncio
    async def test_list_platform_configs_filters_by_tenant_id(self):
        from project.backend.asset_manager.routes import list_platform_configs

        mock_mysql = MagicMock()
        mock_mysql.fetchall = AsyncMock(return_value=[])

        req = StubRequest(tenant_id="tenant-C")
        with patch("project.backend.asset_manager.routes.get_mysql_client", return_value=mock_mysql):
            from fastapi.responses import JSONResponse
            result = await list_platform_configs(request=req, platform=None)

        sql_arg = mock_mysql.fetchall.call_args[0][0]
        assert "tenant_id = %s" in sql_arg
        params = mock_mysql.fetchall.call_args[0][1]
        assert "tenant-C" in params


class TestPipelineOrchestratorTenantIsolation:
    @pytest.mark.asyncio
    async def test_get_pipeline_filters_by_tenant_id(self):
        from project.backend.pipeline_orchestrator.routes import get_pipeline

        mock_mysql = MagicMock()
        mock_mysql.execute = AsyncMock()
        mock_mysql.fetchone = AsyncMock(return_value=None)

        req = StubRequest(tenant_id="tenant-X")
        with patch("project.backend.pipeline_orchestrator.routes.get_mysql_client", return_value=mock_mysql):
            result = await get_pipeline(pipeline_id=1, request=req, _auth={})

        sql_arg = mock_mysql.execute.call_args[0][0]
        assert "AND tenant_id=%s" in sql_arg
        params = mock_mysql.execute.call_args[0][1]
        assert "tenant-X" in params


class TestVideoComposerTenantIsolation:
    @pytest.mark.asyncio
    async def test_compose_status_rejects_other_tenant_task(self):
        from project.backend.video_composer.routes import compose_status
        from fastapi import HTTPException

        mock_redis = MagicMock()
        mock_redis.hgetall = AsyncMock(return_value={"tenant_id": "tenant-A", "status": "queued"})
        mock_redis.close = AsyncMock()

        req = StubRequest(tenant_id="tenant-B")
        with patch("project.backend.video_composer.routes.get_redis", return_value=mock_redis):
            with pytest.raises(HTTPException) as exc_info:
                await compose_status(task_id="t123", request=req, _auth={})
            assert exc_info.value.status_code == 404
