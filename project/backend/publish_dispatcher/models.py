from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    pipeline_id: str
    video_url: str
    platforms: list[str]
    title: str
    description: str | None = None
    tags: list[str] | None = None
    scheduled_time: str | None = None
    tenant_id: str = "default"


class PublishResponse(BaseModel):
    task_id: str
    platform_tasks: list[dict] = Field(default_factory=list)


class PublishLogEntry(BaseModel):
    id: int
    pipeline_id: str
    platform: str
    platform_post_id: str | None = None
    status: str
    public_url: str | None = None
    error_message: str | None = None
