from pydantic import BaseModel, Field


class DashboardStats(BaseModel):
    total_products: int = 0
    hot_products: int = 0
    active_pipelines: int = 0
    total_publishes: int = 0
    total_cost: float = 0.0
    model_usage: dict = Field(default_factory=dict)


class ApiKeyCreate(BaseModel):
    name: str
    scopes: list[str] | None = None
    max_concurrency: int = 10


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str] | None
    enabled: bool
    last_used_at: str | None
    created_at: str


class PlatformOAuthUrl(BaseModel):
    platform: str
    auth_url: str


class TenantConfigUpdate(BaseModel):
    config_key: str
    config_value: str
