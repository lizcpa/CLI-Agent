from __future__ import annotations

import asyncio
import json
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


def test_douyin_crawler_with_mock_playwright():
    from platform_connectors.models import CrawlRequest, PlatformAdapterConfig
    from project.backend.crawl_scheduler.connectors.douyin_crawler import DouyinCrawler

    cfg = {
        "crawler_mapping": json.dumps(
            {
                "title": "title",
                "platform_product_id": "platform_product_id",
                "main_image_url": "main_image_url",
            }
        ),
    }
    adapter_cfg = PlatformAdapterConfig(
        platform_id="douyin", connector_class="DouyinCrawler", config=cfg
    )
    crawler = DouyinCrawler(adapter_cfg)

    fake_data = [
        {
            "title": "Test Product 1",
            "platform_product_id": "p1",
            "main_image_url": "http://img/1.jpg",
        },
        {
            "title": "Test Product 2",
            "platform_product_id": "p2",
            "main_image_url": "http://img/2.jpg",
        },
    ]
    with patch("platform_connectors._playwright.render_page", new=AsyncMock(return_value=fake_data)):
        request = CrawlRequest(
            keyword="test", max_count=10, sort_by="sales", platform_config=cfg
        )
        result = asyncio.run(crawler.crawl(request))

    assert result.total_found == 2
    assert all(p.platform == "douyin" for p in result.products)
    assert result.products[0].title == "Test Product 1"
    assert result.products[0].platform_product_id == "p1"


def test_taobao_crawler_fail_soft_on_none_render():
    from platform_connectors.models import CrawlRequest, PlatformAdapterConfig
    from project.backend.crawl_scheduler.connectors.taobao_crawler import TaobaoCrawler

    adapter_cfg = PlatformAdapterConfig(
        platform_id="taobao", connector_class="TaobaoCrawler", config={}
    )
    crawler = TaobaoCrawler(adapter_cfg)

    with patch("platform_connectors._playwright.render_page", new=AsyncMock(return_value=None)):
        request = CrawlRequest(
            keyword="test", max_count=10, sort_by="sales", platform_config={}
        )
        result = asyncio.run(crawler.crawl(request))

    assert result.total_found == 0
    assert result.products == []


def test_amazon_crawler_with_mock_httpx():
    from platform_connectors.models import CrawlRequest, PlatformAdapterConfig
    from project.backend.crawl_scheduler.connectors.amazon_crawler import AmazonCrawler

    cfg = {
        "crawler_mapping": json.dumps(
            {
                "title": "$.ItemInfo.Title.DisplayValue",
                "platform_product_id": "ASIN",
                "main_image_url": "$.Images.Primary.MediumURL",
            }
        ),
    }
    adapter_cfg = PlatformAdapterConfig(
        platform_id="amazon", connector_class="AmazonCrawler", config=cfg
    )
    crawler = AmazonCrawler(adapter_cfg)

    mock_resp = _make_mock_resp(
        {
            "SearchResult": {
                "Items": [
                    {
                        "ASIN": "B001",
                        "ItemInfo": {"Title": {"DisplayValue": "Amazon Product 1"}},
                        "Images": {"Primary": {"MediumURL": "http://img/1.jpg"}},
                    },
                    {
                        "ASIN": "B002",
                        "ItemInfo": {"Title": {"DisplayValue": "Amazon Product 2"}},
                        "Images": {"Primary": {"MediumURL": "http://img/2.jpg"}},
                    },
                ]
            }
        }
    )
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch(
        "project.backend.crawl_scheduler.connectors.amazon_crawler.httpx.AsyncClient",
        return_value=mock_client,
    ):
        request = CrawlRequest(
            keyword="test", max_count=10, sort_by="sales", platform_config=cfg
        )
        result = asyncio.run(crawler.crawl(request))

    assert result.total_found == 2
    assert result.products[0].title == "Amazon Product 1"
    assert result.products[0].platform_product_id == "B001"
    assert result.products[0].platform == "amazon"


def test_persist_products_upsert():
    from platform_connectors.models import StandardProduct
    from project.backend.crawl_scheduler.persistence import persist_products

    mock_mysql = MagicMock()
    mock_mysql.execute_many = AsyncMock(return_value=2)
    mock_mysql.fetchall = AsyncMock(return_value=[])

    products = [
        StandardProduct(
            platform="douyin",
            platform_product_id="p1",
            title="Product 1",
            main_image_url="http://img/1.jpg",
        ),
        StandardProduct(
            platform="douyin",
            platform_product_id="p2",
            title="Product 2",
            main_image_url="http://img/2.jpg",
        ),
    ]

    with patch(
        "project.backend.crawl_scheduler.persistence.get_mysql_client",
        return_value=mock_mysql,
    ):
        count, product_ids = asyncio.run(persist_products(products, "default"))

    assert count == 2
    mock_mysql.execute_many.assert_called_once()
    call_args = mock_mysql.execute_many.call_args
    sql = call_args.args[0]
    rows = call_args.args[1]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "INSERT INTO products" in sql
    assert len(rows) == 2
    # each row has 15 values matching the column count
    assert len(rows[0]) == 15
    # score/tier must NOT be in the INSERT columns
    assert "score" not in sql.lower().split("on duplicate")[0]
