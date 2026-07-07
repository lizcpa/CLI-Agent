from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.asyncio
async def test_adapt_route_reads_platform_config():
    from project.backend.asset_manager.routes import _get_platform_dims

    mock_mysql = MagicMock()
    mock_mysql.fetchall = AsyncMock(return_value=[
        {"config_key": "width", "config_value": "1280"},
        {"config_key": "height", "config_value": "720"},
        {"config_key": "max_duration", "config_value": "30"},
    ])

    with patch("project.backend.asset_manager.routes.get_mysql_client", return_value=mock_mysql):
        w, h, d = await _get_platform_dims("tiktok")

    assert w == 1280
    assert h == 720
    assert d == 30


@pytest.mark.asyncio
async def test_adapt_route_falls_back_to_defaults():
    from project.backend.asset_manager.routes import _get_platform_dims

    mock_mysql = MagicMock()
    mock_mysql.fetchall = AsyncMock(side_effect=Exception("DB down"))

    with patch("project.backend.asset_manager.routes.get_mysql_client", return_value=mock_mysql):
        w, h, d = await _get_platform_dims("youtube")

    assert w == 1920
    assert h == 1080
    assert d == 0
