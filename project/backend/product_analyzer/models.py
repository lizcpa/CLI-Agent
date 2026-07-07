from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    product_ids: list[int] | None = None
    platform: str | None = None
    limit: int = 100


class AnalyzeResponse(BaseModel):
    task_id: str
    analyzed_count: int
    hot_count: int


class ProductScore(BaseModel):
    product_id: int
    title: str
    platform: str
    score: float
    tier: str
    dimensions: dict[str, float]


class ScoreConfig(BaseModel):
    score_threshold: float = 70.0
    weight_hotness: float = 0.4
    weight_conversion: float = 0.35
    weight_profit: float = 0.25


class TenantScoreConfig(BaseModel):
    tenant_id: str = "default"
    score_threshold: float = 70.0
    weight_hotness: float = 0.4
    weight_conversion: float = 0.35
    weight_profit: float = 0.25
