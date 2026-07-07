"""Shared FFmpeg wrappers. All functions are synchronous and meant to be called inside Celery tasks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(cmd, timeout=timeout, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace")[-500:]
        raise FFmpegError(f"ffmpeg failed (rc={proc.returncode}): {tail}")


def concat_clips(clip_paths: list[Path], output_path: Path) -> None:
    if len(clip_paths) == 1:
        _run(["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(output_path)])
        return
    list_path = output_path.parent / "concat_list.txt"
    list_path.write_text(
        "\n".join(f"file '{p.absolute()}'" for p in clip_paths),
        encoding="utf-8",
    )
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_path), "-c", "copy", str(output_path),
    ])


def mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None:
    _run([
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        "-map", "0:v:0", "-map", "1:a:0", str(output_path),
    ])


def burn_subtitle(
    video_path: Path,
    subtitle_text: str,
    output_path: Path,
    style: Optional[dict] = None,
) -> None:
    style = style or {"FontSize": 24, "PrimaryColour": "&Hffffff&", "Outline": 2}
    srt_path = output_path.parent / "subtitle.srt"
    srt_content = (
        "1\n00:00:00,000 --> 99:59:59,000\n" + subtitle_text + "\n"
    )
    srt_path.write_text(srt_content, encoding="utf-8")
    style_str = ",".join(f"{k}={v}" for k, v in style.items())
    filter_str = f"subtitles='{srt_path.absolute()}':force_style='{style_str}'"
    _run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", filter_str, "-c:a", "copy", str(output_path),
    ])


def transcode_scale(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int,
    max_duration: int = 0,
    drop_audio: bool = False,
) -> Path:
    cover_path = output_path.with_suffix(".jpg")
    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(input_path), "-vf", scale_filter]
    if max_duration > 0:
        cmd += ["-t", str(max_duration)]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    if drop_audio:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac"]
    cmd.append(str(output_path))
    _run(cmd)
    _run([
        "ffmpeg", "-y", "-i", str(output_path),
        "-vframes", "1", "-q:v", "2", str(cover_path),
    ])
    return cover_path
