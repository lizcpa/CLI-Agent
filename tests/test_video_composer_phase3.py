from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_ffmpeg_helper_concat_single_clip(tmp_path):
    from ffmpeg_helper import concat_clips

    clip = tmp_path / "clip.mp4"
    clip.write_text("fake")
    out = tmp_path / "out.mp4"

    with patch("ffmpeg_helper.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        concat_clips([clip], out)

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-c" in cmd and "copy" in cmd
    assert "-f" not in cmd


def test_ffmpeg_helper_concat_multiple_clips(tmp_path):
    from ffmpeg_helper import concat_clips

    clips = [tmp_path / f"clip_{i}.mp4" for i in range(2)]
    for c in clips:
        c.write_text("fake")
    out = tmp_path / "out.mp4"

    with patch("ffmpeg_helper.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        concat_clips(clips, out)

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-f" in cmd and "concat" in cmd
    assert (tmp_path / "concat_list.txt").exists()


def test_ffmpeg_helper_burn_subtitle_generates_srt(tmp_path):
    from ffmpeg_helper import burn_subtitle

    video = tmp_path / "in.mp4"
    video.write_text("fake")
    out = tmp_path / "out.mp4"

    with patch("ffmpeg_helper.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        burn_subtitle(video, "hello world", out)

    srt = tmp_path / "subtitle.srt"
    assert srt.exists()
    srt_content = srt.read_text(encoding="utf-8")
    assert "hello world" in srt_content
    cmd = mock_run.call_args[0][0]
    assert any("subtitles" in str(c) for c in cmd)


def test_compose_video_task_downloads_and_uploads(tmp_path):
    from project.backend.video_composer import tasks
    from mq_clients.celery_app import BaseTask

    mock_minio = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_redis = MagicMock()

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_minio_client", return_value=mock_minio), \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "concat_clips", side_effect=lambda c, o: o.write_text("fake")), \
         patch.object(tasks, "mux_audio"), \
         patch.object(tasks, "burn_subtitle"):
        mock_rc.return_value = mock_redis
        result = tasks.compose_video_task.run(
            task_id="t1", pipeline_id="p1",
            video_clips=["prodvideofactory/c1.mp4", "prodvideofactory/c2.mp4"],
            images=[], audio_url=None, subtitle_text=None,
            template_id=None, config=None, tenant_id="default",
        )

    assert "output_object" in result
    assert result["output_object"] == "prodvideofactory/final/default/p1/output.mp4"
    assert mock_minio.download_file.call_count == 2
    assert mock_minio.upload_file.called
    mysql_calls = mock_mysql.execute.call_args_list
    assert any("UPDATE generation_pipelines" in str(c) for c in mysql_calls)


def test_compose_video_task_updates_pipeline_failed_on_error(tmp_path):
    from project.backend.video_composer import tasks
    from mq_clients.celery_app import BaseTask

    mock_minio = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_redis = MagicMock()

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_minio_client", return_value=mock_minio), \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "concat_clips", side_effect=RuntimeError("ffmpeg died")):
        mock_rc.return_value = mock_redis
        with pytest.raises(RuntimeError):
            tasks.compose_video_task.run(
                task_id="t1", pipeline_id="p1",
                video_clips=["c1.mp4"], images=[],
                audio_url=None, subtitle_text=None,
                template_id=None, config=None, tenant_id="default",
            )

    mysql_calls = mock_mysql.execute.call_args_list
    assert any("compose_status" in str(c) and "failed" in str(c) for c in mysql_calls)
