from __future__ import annotations

from pydantic import BaseModel, Field


class CopywritingRequest(BaseModel):
    product_id: int
    product_title: str
    product_desc: str | None = None
    keywords: list[str] = Field(default_factory=list)
    style: str = "marketing"
    max_length: int = 200
    model: str | None = None


class CopywritingResponse(BaseModel):
    task_id: str
    text: str | None = None


class ImageGenerateRequest(BaseModel):
    prompts: list[str]
    size: str = "1024x1024"
    n: int = 1
    negative_prompt: str | None = None
    model: str | None = None
    seed: int | None = None


class ImageGenerateResponse(BaseModel):
    task_id: str


class VideoGenerateRequest(BaseModel):
    type: str
    prompts: list[str]
    reference_image_url: str | None = None
    duration: int = 5
    resolution: str = "1080p"
    count: int = 1
    motion_strength: float = 0.5
    model: str | None = None


class VideoGenerateResponse(BaseModel):
    task_id: str


class TTSRequest(BaseModel):
    text: str
    voice: str = "default"
    language: str = "zh"
    speed: float = 1.0


class TTSResponse(BaseModel):
    task_id: str


class ModelInfo(BaseModel):
    id: str
    type: str
    name: str
    is_healthy: bool
    capabilities: dict


class UsageLogEntry(BaseModel):
    adapter_id: str
    adapter_type: str
    model: str
    pipeline_id: str = ""
    tenant_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0
    duration_seconds: float = 0.0
    estimated_cost: float = 0.0
    status: str = "success"
