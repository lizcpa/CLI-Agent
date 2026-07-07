from __future__ import annotations

from pydantic import BaseModel, Field


class CrawlJobRequest(BaseModel):
    platform: str
    keyword: str
    max_count: int = 100
    sort_by: str = "sales"


class CrawlJobResponse(BaseModel):
    job_id: str
    task_id: str
    status: str
    estimated_seconds: int = 30


class CrawlJobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    products_found: int = 0
    error: str | None = None


class ConnectorInfo(BaseModel):
    platform_id: str
    connector_class: str
    proxy_required: bool
    rate_limit: str


class CrawlPlanCreate(BaseModel):
    name: str
    platform: str
    keyword: str
    category: str = ""
    max_count: int = 100
    sort_by: str = "sales"
    cron_expression: str | None = None


class CrawlPlanUpdate(BaseModel):
    name: str | None = None
    keyword: str | None = None
    max_count: int | None = None
    sort_by: str | None = None
    cron_expression: str | None = None
    enabled: bool | None = None
