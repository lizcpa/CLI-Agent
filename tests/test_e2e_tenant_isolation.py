"""E2E tests for cross-tenant isolation."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import StubRequest


class TestVideoComposerTenantIsolation:
    """Test video composer cross-tenant rejection."""

    @pytest.mark.asyncio
    async def test_compose_status_rejects_cross_tenant_task(self):
        """tenant-B cannot query tenant-A's compose task."""
        from project.backend.video_composer.routes import compose_status

        mock_redis = MagicMock()
        mock_redis.hgetall = AsyncMock(return_value={
            "tenant_id": "tenant-A",
            "status": "completed",
        })
        mock_redis.close = AsyncMock()

        req_b = StubRequest(tenant_id="tenant-B")

        with patch("project.backend.video_composer.routes.get_redis", return_value=mock_redis):
            with pytest.raises(HTTPException) as exc_info:
                await compose_status(task_id="task_A", request=req_b, _auth={})

        assert exc_info.value.status_code == 404


class TestPublishDispatcherTenantIsolation:
    """Test publish dispatcher cross-tenant rejection."""

    @pytest.mark.asyncio
    async def test_publish_dispatcher_rejects_cross_tenant_publish(self):
        """tenant-B cannot publish tenant-A's pipeline."""
        from project.backend.publish_dispatcher.routes import get_publish_status

        mock_mysql = MagicMock()
        mock_mysql.fetchone = AsyncMock(return_value=None)

        req_b = StubRequest(tenant_id="tenant-B")

        with patch("project.backend.publish_dispatcher.routes.get_mysql_client", return_value=mock_mysql):
            result = await get_publish_status(task_id="pub_A", request=req_b)

        assert result.status_code == 404