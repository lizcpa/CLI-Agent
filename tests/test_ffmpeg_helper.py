from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))


class TestConcatClips:
    """concat_clips(clip_paths, output_path) 测试"""

    def test_concat_single_clip(self, tmp_path):
        """1 个片段：直接 copy 不生成 concat 文件"""
        from ffmpeg_helper import concat_clips

        input_paths = [tmp_path / "clip1.mp4"]
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concat_clips(input_paths, output_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == str(input_paths[0])
        assert cmd[-1] == str(output_path)

        # 没有 concat_list.txt 被创建
        assert not (tmp_path / "concat_list.txt").exists()

    def test_concat_multiple_clips(self, tmp_path):
        """多个片段：生成 concat_list.txt + ffmpeg concat"""
        from ffmpeg_helper import concat_clips

        input_paths = [tmp_path / f"clip{i}.mp4" for i in range(3)]
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            concat_clips(input_paths, output_path)

        # 验证 concat_list.txt 已被创建且内容正确
        list_path = tmp_path / "concat_list.txt"
        assert list_path.exists()
        content = list_path.read_text(encoding="utf-8")
        for p in input_paths:
            assert f"file '{p.absolute()}'" in content

        # 验证 ffmpeg 使用了 concat 模式
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "concat" in cmd
        assert "-safe" in cmd

    def test_concat_ffmpeg_failure_raises_error(self, tmp_path):
        """subprocess 返回非零 → FFmpegError"""
        from ffmpeg_helper import concat_clips, FFmpegError

        input_paths = [tmp_path / "clip1.mp4"]
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            mock_run.return_value.stderr.decode.return_value = "some error"
            with pytest.raises(FFmpegError):
                concat_clips(input_paths, output_path)


class TestMuxAudio:
    """mux_audio(video_path, audio_path, output_path) 测试"""

    def test_mux_audio_correct_command(self, tmp_path):
        """验证 ffmpeg 命令参数正确"""
        from ffmpeg_helper import mux_audio

        video_path = tmp_path / "video.mp4"
        audio_path = tmp_path / "audio.aac"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mux_audio(video_path, audio_path, output_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert str(video_path) in cmd
        assert str(audio_path) in cmd
        assert "-c:v" in cmd
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "-map" in cmd
        assert output_path == Path(cmd[-1])

    def test_mux_audio_failure(self, tmp_path):
        """错误时抛出 FFmpegError"""
        from ffmpeg_helper import mux_audio, FFmpegError

        video_path = tmp_path / "video.mp4"
        audio_path = tmp_path / "audio.aac"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            mock_run.return_value.stderr.decode.return_value = "mux error"
            with pytest.raises(FFmpegError):
                mux_audio(video_path, audio_path, output_path)


class TestBurnSubtitle:
    """burn_subtitle(video_path, subtitle_text, output_path, style=None) 测试"""

    def test_burn_subtitle_creates_srt(self, tmp_path):
        """验证生成了 srt 文件"""
        from ffmpeg_helper import burn_subtitle

        video_path = tmp_path / "video.mp4"
        output_path = tmp_path / "output.mp4"
        subtitle_text = "Hello World"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            burn_subtitle(video_path, subtitle_text, output_path)

        # 验证 .srt 文件已创建
        srt_path = tmp_path / "subtitle.srt"
        assert srt_path.exists()
        content = srt_path.read_text(encoding="utf-8")
        assert subtitle_text in content

        # 验证 ffmpeg 使用了 subtitles filter
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        vf_idx = cmd.index("-vf")
        filter_str = cmd[vf_idx + 1]
        assert "subtitles=" in filter_str

    def test_burn_subtitle_with_custom_style(self, tmp_path):
        """自定义样式"""
        from ffmpeg_helper import burn_subtitle

        video_path = tmp_path / "video.mp4"
        output_path = tmp_path / "output.mp4"
        custom_style = {"FontSize": 48, "PrimaryColour": "&H00ff00&", "Outline": 1}

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            burn_subtitle(video_path, "test", output_path, style=custom_style)

        cmd = mock_run.call_args[0][0]
        vf_idx = cmd.index("-vf")
        filter_str = cmd[vf_idx + 1]
        for k, v in custom_style.items():
            assert f"{k}={v}" in filter_str

    def test_burn_subtitle_default_style(self, tmp_path):
        """默认样式"""
        from ffmpeg_helper import burn_subtitle

        video_path = tmp_path / "video.mp4"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            burn_subtitle(video_path, "test", output_path)

        cmd = mock_run.call_args[0][0]
        vf_idx = cmd.index("-vf")
        filter_str = cmd[vf_idx + 1]
        assert "FontSize=24" in filter_str
        assert "PrimaryColour=&Hffffff&" in filter_str
        assert "Outline=2" in filter_str


class TestTranscodeScale:
    """transcode_scale(input_path, output_path, width, height, ...) 测试"""

    def test_transcode_scale_basic(self, tmp_path):
        """基本转码"""
        from ffmpeg_helper import transcode_scale

        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = transcode_scale(input_path, output_path, 1920, 1080)

        # 应该调用两次 ffmpeg：转码 + 封面提取
        assert mock_run.call_count == 2

        # 第一次调用：转码
        cmd1 = mock_run.call_args_list[0][0][0]
        assert cmd1[0] == "ffmpeg"
        vf_idx = cmd1.index("-vf")
        assert "scale=1920:1080" in cmd1[vf_idx + 1]
        assert "-c:v" in cmd1
        assert "libx264" in cmd1
        assert "-c:a" in cmd1
        assert "aac" in cmd1
        assert "-an" not in cmd1
        assert output_path == Path(cmd1[-1])

        # 第二次调用：封面提取
        cmd2 = mock_run.call_args_list[1][0][0]
        assert "-vframes" in cmd2
        assert "1" in cmd2

        # 验证返回值
        assert result == output_path.with_suffix(".jpg")

    def test_transcode_scale_with_max_duration(self, tmp_path):
        """限制时长"""
        from ffmpeg_helper import transcode_scale

        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            transcode_scale(input_path, output_path, 1920, 1080, max_duration=30)

        cmd1 = mock_run.call_args_list[0][0][0]
        assert "-t" in cmd1
        t_idx = cmd1.index("-t")
        assert cmd1[t_idx + 1] == "30"

    def test_transcode_scale_drop_audio(self, tmp_path):
        """丢弃音轨"""
        from ffmpeg_helper import transcode_scale

        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            transcode_scale(input_path, output_path, 1280, 720, drop_audio=True)

        cmd1 = mock_run.call_args_list[0][0][0]
        assert "-an" in cmd1
        assert "-c:a" not in cmd1

    def test_transcode_scale_returns_cover_path(self, tmp_path):
        """验证返回封面路径"""
        from ffmpeg_helper import transcode_scale

        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"

        with patch("ffmpeg_helper.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = transcode_scale(input_path, output_path, 640, 480)

        expected_cover = output_path.with_suffix(".jpg")
        assert result == expected_cover
        assert result.suffix == ".jpg"
