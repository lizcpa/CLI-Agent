import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from mq_clients.celery_app import create_task, BaseTask
from db_clients.minio import get_minio_client
from db_clients.mysql import get_mysql_client
from common_sdk.business_metrics import video_compose_jobs_total
from common_sdk.logger import get_logger
from ffmpeg_helper import concat_clips, mux_audio, burn_subtitle

from .config import MINIO_BUCKET

logger = get_logger(__name__)


def _set_status(task: BaseTask, task_id: str, **fields) -> None:
    task.redis_client.hset(f"task:{task_id}", mapping=fields)
    task.redis_client.expire(f"task:{task_id}", 86400)


def _download_object(obj_name: str, dest: Path) -> None:
    minio = get_minio_client()
    bucket = MINIO_BUCKET
    if "/" in obj_name and obj_name.split("/", 1)[0] == MINIO_BUCKET:
        obj_name = obj_name.split("/", 1)[1]
    minio.download_file(bucket, obj_name, str(dest))


def _update_pipeline(pipeline_id: str, **fields) -> None:
    try:
        mysql = get_mysql_client()
        cols = ", ".join(f"{k}=%s" for k in fields)
        params = list(fields.values()) + [pipeline_id]

        async def _do():
            await mysql.execute(
                f"UPDATE generation_pipelines SET {cols}, updated_at=NOW() WHERE id=%s",
                tuple(params),
            )

        asyncio.run(_do())
    except Exception as e:
        logger.warning("update_pipeline_failed", pipeline_id=pipeline_id, error=str(e))


@create_task("compose_video", queue="compose_queue")
def compose_video_task(
    self,
    task_id,
    pipeline_id,
    video_clips,
    images,
    audio_url,
    subtitle_text,
    template_id,
    config,
    tenant_id="default",
):
    _set_status(self, task_id, status="running", progress_percent="5")
    temp_dir = Path(tempfile.gettempdir()) / f"compose_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        clip_paths: list[Path] = []
        for i, obj in enumerate(video_clips):
            p = temp_dir / f"clip_{i:03d}.mp4"
            _download_object(obj, p)
            clip_paths.append(p)
        _set_status(self, task_id, progress_percent="30")

        concat_path = temp_dir / "concat.mp4"
        concat_clips(clip_paths, concat_path)
        current_path = concat_path
        _set_status(self, task_id, progress_percent="55")

        if audio_url:
            audio_path = temp_dir / "audio.mp3"
            _download_object(audio_url, audio_path)
            muxed_path = temp_dir / "with_audio.mp4"
            mux_audio(current_path, audio_path, muxed_path)
            current_path = muxed_path
        _set_status(self, task_id, progress_percent="70")

        if subtitle_text:
            sub_path = temp_dir / "with_sub.mp4"
            burn_subtitle(current_path, subtitle_text, sub_path)
            current_path = sub_path
        _set_status(self, task_id, progress_percent="85")

        output_obj = f"final/{tenant_id}/{pipeline_id}/output.mp4"
        minio = get_minio_client()
        minio.upload_file(MINIO_BUCKET, output_obj, str(current_path), "video/mp4")

        _update_pipeline(
            pipeline_id,
            final_video_url=f"{MINIO_BUCKET}/{output_obj}",
            compose_status="completed",
        )

        result = {"output_object": f"{MINIO_BUCKET}/{output_obj}"}
        _set_status(
            self,
            task_id,
            status="completed",
            progress_percent="100",
            result=json.dumps(result, ensure_ascii=False),
        )
        video_compose_jobs_total.labels(status="success").inc()
        return result
    except Exception as e:
        video_compose_jobs_total.labels(status="failed").inc()
        _set_status(self, task_id, status="failed", error=str(e))
        _update_pipeline(
            pipeline_id,
            compose_status="failed",
            error_message=str(e)[:500],
        )
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
