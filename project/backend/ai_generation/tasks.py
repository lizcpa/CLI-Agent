from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json
import time

from mq_clients.celery_app import celery_app, BaseTask

from common_sdk.business_metrics import ai_generation_requests_total, ai_generation_duration_seconds
from common_sdk.content_safety import content_safety_client
from common_sdk.exceptions import ContentFilteredException

from .worker_router import worker_router
from .adapters._minio_helper import presigned_url


def _set_status(self, task_id: str, **fields) -> None:
    self.redis_client.hset(f"task:{task_id}", mapping=fields)


def _check_text_safety(self, task_id: str, text: str) -> None:
    safety = content_safety_client.check_text(text)
    if not safety.passed:
        _set_status(
            self,
            task_id,
            status="content_filtered",
            error=f"content filtered: {safety.detail}",
            result=json.dumps({"risk_level": safety.risk_level}, ensure_ascii=False),
        )
        raise ContentFilteredException(message=safety.detail)


@celery_app.task(
    bind=True,
    name="ai_generation.tasks.generate_copywriting_task",
    queue="ai_queue",
    max_retries=3,
    base=BaseTask,
)
def generate_copywriting_task(
    self,
    task_id: str,
    product_id: int,
    product_title: str,
    product_desc: str | None = None,
    keywords: list[str] | None = None,
    style: str = "marketing",
    max_length: int = 200,
    model: str | None = None,
    tenant_id: str = "default",
    pipeline_id: str = "",
) -> dict:
    keywords = keywords or []
    _set_status(self, task_id, status="running", progress_percent="10")

    start_ts = time.monotonic()
    adapter_type = "llm"
    try:
        prompt_text = (
            f"产品标题：{product_title}\n"
            f"产品描述：{product_desc or '高品质产品'}\n"
            f"关键词：{', '.join(keywords)}\n"
            f"风格：{style}\n"
            f"最大长度：{max_length}"
        )
        messages = [
            {"role": "system", "content": "你是专业的电商营销文案撰写专家，请根据产品信息生成吸引人的营销文案。"},
            {"role": "user", "content": prompt_text},
        ]

        adapter = worker_router.route_llm(preferred_model=model, product_tier="normal")
        if adapter is None:
            raise RuntimeError("No healthy LLM adapter available")

        result = asyncio.run(
            adapter.chat_async(messages=messages, max_tokens=max_length, temperature=0.7)
        )
        text = result.get("text", "")
        if len(text) > max_length:
            text = text[:max_length]

        _check_text_safety(self, task_id, text)

        _set_status(
            self,
            task_id,
            status="completed",
            progress_percent="100",
            result=json.dumps({"text": text}, ensure_ascii=False),
        )
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="success"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        return {"text": text}
    except Exception as e:
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="failed"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        _set_status(self, task_id, status="failed", error=str(e))
        raise


@celery_app.task(
    bind=True,
    name="ai_generation.tasks.generate_images_task",
    queue="ai_queue",
    max_retries=3,
    base=BaseTask,
)
def generate_images_task(
    self,
    task_id: str,
    prompts: list[str],
    size: str = "1024x1024",
    n: int = 1,
    negative_prompt: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    tenant_id: str = "default",
    pipeline_id: str = "",
) -> dict:
    _set_status(self, task_id, status="running", progress_percent="10")

    start_ts = time.monotonic()
    adapter_type = "image"
    try:
        adapter = worker_router.route_image(preferred_model=model, product_tier="normal")
        if adapter is None:
            raise RuntimeError("No healthy Image adapter available")

        result = asyncio.run(
            adapter.generate_async(
                prompts=prompts,
                size=size,
                n=n,
                negative_prompt=negative_prompt,
                seed=seed,
                tenant_id=tenant_id,
                pipeline_id=pipeline_id,
            )
        )
        image_objects: list[str] = result.get("image_objects", [])

        for obj in image_objects:
            img_url = presigned_url(obj)
            img_safety = content_safety_client.check_image(img_url)
            if not img_safety.passed:
                _set_status(
                    self,
                    task_id,
                    status="content_filtered",
                    error=f"content filtered: {img_safety.detail}",
                    result=json.dumps({"risk_level": img_safety.risk_level}, ensure_ascii=False),
                )
                raise ContentFilteredException(message=img_safety.detail)

        _set_status(
            self,
            task_id,
            status="completed",
            progress_percent="100",
            result=json.dumps({"image_objects": image_objects}, ensure_ascii=False),
        )
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="success"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        return {"image_objects": image_objects}
    except Exception as e:
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="failed"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        _set_status(self, task_id, status="failed", error=str(e))
        raise


@celery_app.task(
    bind=True,
    name="ai_generation.tasks.generate_video_clips_task",
    queue="ai_queue",
    max_retries=3,
    base=BaseTask,
)
def generate_video_clips_task(
    self,
    task_id: str,
    video_type: str,
    prompts: list[str],
    reference_image_url: str | None = None,
    duration: int = 5,
    resolution: str = "1080p",
    count: int = 1,
    motion_strength: float = 0.5,
    model: str | None = None,
    tenant_id: str = "default",
    pipeline_id: str = "",
) -> dict:
    _set_status(self, task_id, status="running", progress_percent="10")

    start_ts = time.monotonic()
    adapter_type = "video"
    try:
        for prompt in prompts:
            _check_text_safety(self, task_id, prompt)

        adapter = worker_router.route_video(preferred_model=model, product_tier="normal")
        if adapter is None:
            raise RuntimeError("No healthy Video adapter available")

        result = asyncio.run(
            adapter.generate_async(
                type=video_type,
                prompts=prompts,
                reference_image_url=reference_image_url,
                duration=duration,
                resolution=resolution,
                count=count,
                motion_strength=motion_strength,
                tenant_id=tenant_id,
                pipeline_id=pipeline_id,
                task_id=task_id,
            )
        )
        clip_objects: list[str] = result.get("clip_objects", [])

        _set_status(
            self,
            task_id,
            status="completed",
            progress_percent="100",
            result=json.dumps({"clip_objects": clip_objects}, ensure_ascii=False),
        )
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="success"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        return {"clip_objects": clip_objects}
    except Exception as e:
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model=model or "auto", status="failed"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        _set_status(self, task_id, status="failed", error=str(e))
        raise


@celery_app.task(
    bind=True,
    name="ai_generation.tasks.tts_synthesize_task",
    queue="ai_queue",
    max_retries=3,
    base=BaseTask,
)
def tts_synthesize_task(
    self,
    task_id: str,
    text: str,
    voice: str = "default",
    language: str = "zh",
    speed: float = 1.0,
    tenant_id: str = "default",
    pipeline_id: str = "",
) -> dict:
    _set_status(self, task_id, status="running", progress_percent="10")

    start_ts = time.monotonic()
    adapter_type = "tts"
    try:
        _check_text_safety(self, task_id, text)

        adapter = worker_router.route_tts(preferred_model=None)
        if adapter is None:
            raise RuntimeError("No healthy TTS adapter available")

        result = asyncio.run(
            adapter.synthesize_async(
                text=text,
                voice=voice,
                language=language,
                speed=speed,
                tenant_id=tenant_id,
                pipeline_id=pipeline_id,
            )
        )
        audio_object: str = result.get("audio_object", "")

        _set_status(
            self,
            task_id,
            status="completed",
            progress_percent="100",
            result=json.dumps({"audio_object": audio_object}, ensure_ascii=False),
        )
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model="auto", status="success"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        return {"audio_object": audio_object}
    except Exception as e:
        ai_generation_requests_total.labels(
            adapter_type=adapter_type, model="auto", status="failed"
        ).inc()
        ai_generation_duration_seconds.labels(adapter_type=adapter_type).observe(
            time.monotonic() - start_ts
        )
        _set_status(self, task_id, status="failed", error=str(e))
        raise
