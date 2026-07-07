from pydantic import BaseModel, Field


class VideoAdaptRequest(BaseModel):
    video_url: str
    platforms: list[str]


class VideoAdaptResponse(BaseModel):
    task_id: str
    results: list[dict] = Field(default_factory=list)


class PlatformConfigCreate(BaseModel):
    platform: str
    config_key: str
    config_value: str
    description: str | None = None


class TemplateCreate(BaseModel):
    name: str
    content: dict
