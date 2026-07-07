import os
import uuid
import tempfile
import subprocess
import logging
from pathlib import Path

from utils.mq_clients.celery_app import create_task
from utils.db_clients.minio import get_minio_client
from utils.ffmpeg_helper import transcode_scale

logger = logging.getLogger(__name__)


@create_task("adapt_video_for_platform", queue="compose_queue")
def adapt_video_for_platform(
    self,
    task_id: str,
    video_url: str,
    platform: str,
    width: int,
    height: int,
    max_duration: int,
    drop_audio: bool = False,
):
    minio = get_minio_client()
    temp_dir = Path(tempfile.gettempdir()) / f"adapt_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    input_path = temp_dir / f"input_{uuid.uuid4().hex[:8]}.mp4"
    output_path = temp_dir / f"output_{uuid.uuid4().hex[:8]}.mp4"
    try:
        if video_url.startswith("minio://"):
            parts = video_url.replace("minio://", "").split("/", 1)
            if len(parts) == 2:
                bucket, obj = parts
                minio.download_file(bucket, obj, str(input_path))
        else:
            subprocess.run(
                ["curl", "-L", "-o", str(input_path), video_url],
                timeout=120,
                check=True,
                capture_output=True,
            )
        cover_path = transcode_scale(
            input_path, output_path, width, height,
            max_duration=max_duration, drop_audio=drop_audio,
        )
        adapted_obj = f"adapted/{platform}/{task_id}/output.mp4"
        cover_obj = f"adapted/{platform}/{task_id}/cover.jpg"
        minio.upload_file("prodvideofactory", adapted_obj, str(output_path), "video/mp4")
        minio.upload_file("prodvideofactory", cover_obj, str(cover_path), "image/jpeg")
        adapted_url = f"minio://prodvideofactory/{adapted_obj}"
        cover_url = f"minio://prodvideofactory/{cover_obj}"
        return {"adapted_url": adapted_url, "cover_url": cover_url, "platform": platform}
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg failed for task %s: %s", task_id, e.stderr)
        raise
    except Exception as e:
        logger.error("Adapt task %s failed: %s", task_id, str(e))
        raise
    finally:
        cleanup_temp_files(str(temp_dir))


@create_task("cleanup_temp", queue="compose_queue")
def cleanup_temp_files(self, temp_path: str):
    import shutil
    if os.path.exists(temp_path):
        try:
            shutil.rmtree(temp_path)
        except OSError:
            os.remove(temp_path)
