import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest
from platform_connectors import (
    StandardProduct, CrawlRequest, CrawlResult, PublishContent,
    PublishRequest, PublishResult, PlatformAdapterConfig, PlatformDataMapper,
)


class TestModels:
    def test_standard_product_minimal(self):
        p = StandardProduct(platform="douyin", platform_product_id="P123", title="test")
        assert p.platform == "douyin"
        assert p.platform_product_id == "P123"
        assert p.title == "test"
        assert p.price is None
        assert p.sales_count == 0
        assert p.tags == []

    def test_standard_product_full(self):
        p = StandardProduct(
            platform="amazon",
            platform_product_id="A456",
            title="Product X",
            price=29.99,
            currency="USD",
            sales_count=500,
            rating=4.5,
            tags=["hot", "new"],
        )
        assert p.price == 29.99
        assert p.currency == "USD"
        assert p.tags == ["hot", "new"]

    def test_crawl_request(self):
        r = CrawlRequest(keyword="手机", max_count=50, sort_by="price")
        assert r.keyword == "手机"
        assert r.max_count == 50
        assert r.sort_by == "price"

    def test_crawl_result(self):
        products = [
            StandardProduct(platform="test", platform_product_id="1", title="A"),
            StandardProduct(platform="test", platform_product_id="2", title="B"),
        ]
        r = CrawlResult(products=products, total_found=100, crawl_duration_ms=5000)
        assert len(r.products) == 2
        assert r.total_found == 100

    def test_publish_content(self):
        c = PublishContent(video_url="http://v.mp4", title="Great Product")
        assert c.video_url == "http://v.mp4"
        assert c.description is None

    def test_platform_adapter_config(self):
        cfg = PlatformAdapterConfig(
            platform_id="douyin",
            connector_class="crawlers.douyin.DouyinCrawler",
            proxy_required=True,
            rate_limit="10/min",
        )
        assert cfg.proxy_required is True
        assert cfg.rate_limit == "10/min"


class TestDataMapper:
    def test_basic_mapping(self):
        mapper = PlatformDataMapper({
            "test_platform": {
                "title": "$.item.title",
                "price": "$.item.price.value",
                "platform_product_id": "$.item.pid",
                "sales_count": "$.item.sales.count",
                "main_image_url": "$.item.images[0]",
            }
        })
        raw = {
            "item": {
                "pid": "P12345",
                "title": "爆款商品",
                "price": {"value": 99.99},
                "sales": {"count": 5000},
                "images": ["https://img.example.com/1.jpg"],
            }
        }
        product = mapper.map_to_standard("test_platform", raw)
        assert product.title == "爆款商品"
        assert product.price == 99.99
        assert product.sales_count == 5000
        assert product.platform_product_id == "P12345"

    def test_mapping_with_defaults(self):
        mapper = PlatformDataMapper({
            "p": {"title": "$.name", "platform_product_id": "$.id"}
        })
        product = mapper.map_to_standard("p", {"name": "Item", "id": "ID1"})
        assert product.title == "Item"
        assert product.price is None
        assert product.sales_count == 0

    def test_nested_path_not_found(self):
        mapper = PlatformDataMapper({
            "p": {"title": "$.name", "price": "$.missing.deep", "platform_product_id": "$.id"}
        })
        product = mapper.map_to_standard("p", {"name": "X", "id": "1"})
        assert product.title == "X"
        assert product.price is None  # gracefully handles missing path

    def test_batch_mapping(self):
        mapper = PlatformDataMapper({
            "p": {"title": "$.n", "platform_product_id": "$.id"}
        })
        raw_list = [
            {"n": "A", "id": "1"},
            {"n": "B", "id": "2"},
            {"n": "C", "id": "3"},
        ]
        products = mapper.map_batch("p", raw_list)
        assert len(products) == 3
        assert products[0].title == "A"
        assert products[2].title == "C"

    def test_missing_platform_raises(self):
        mapper = PlatformDataMapper({})
        with pytest.raises(ValueError):
            mapper.map_to_standard("unknown", {})

    def test_type_coercion_number(self):
        mapper = PlatformDataMapper({
            "p": {"title": "$.n", "price": "$.p", "platform_product_id": "$.id", "sales_count": "$.s"}
        })
        product = mapper.map_to_standard("p", {"n": "X", "id": "1", "p": "199", "s": "3000"})
        assert product.price == 199.0
        assert product.sales_count == 3000
