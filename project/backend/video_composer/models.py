from pydantic import BaseModel


class ComposeRequest(BaseModel):
    pipeline_id: str
    video_clips: list[str]
    images: list[str]
    audio_url: str | None = None
    subtitle_text: str | None = None
    template_id: str | None = None
    config: dict | None = None


class ComposeResponse(BaseModel):
    task_id: str
    estimated_seconds: int = 60


class ComposeStatus(BaseModel):
    task_id: str
    status: str
    progress: int
    output_url: str | None = None
