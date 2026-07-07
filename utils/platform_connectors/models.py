from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StandardProduct(BaseModel):
    platform: str
    platform_product_id: str
    title: str
    description: str | None = None
    main_image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    price: float | None = None
    currency: str = "CNY"
    sales_count: int = 0
    rating: float = 0.0
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class CrawlRequest(BaseModel):
    keyword: str
    max_count: int = 100
    sort_by: str = "sales"
    platform_config: dict[str, Any] = Field(default_factory=dict)


class CrawlResult(BaseModel):
    products: list[StandardProduct] = Field(default_factory=list)
    total_found: int = 0
    crawl_duration_ms: int = 0


class PublishContent(BaseModel):
    video_url: str
    cover_url: str | None = None
    title: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    product_link: str | None = None


class PublishRequest(BaseModel):
    platform: str
    content: PublishContent
    scheduled_time: datetime | None = None
    platform_config: dict[str, Any] = Field(default_factory=dict)


class PublishResult(BaseModel):
    platform_post_id: str
    public_url: str = ""
    status: str = "pending"
    error_message: str | None = None


class PlatformAdapterConfig(BaseModel):
    platform_id: str
    connector_class: str
    proxy_required: bool = False
    rate_limit: str = "10/min"
    auth_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
