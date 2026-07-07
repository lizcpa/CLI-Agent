# ProdVideo AI Factory — Phase 3 深度开发计划

## 概述

Phase 2（ai_generation 真实化）已完成，19/19 测试通过。本阶段（Phase 3）按架构文档 V4.1 继续向下游推进，深度开发流水线 DAG 中剩余的两个核心节点：

1. **video-composer**（端口 8004）—— 当前完全桩实现，是流水线 DAG 的关键瓶颈（架构文档 L609：三个并行任务完成后触发 `compose_video`）。本阶段实现真实 FFmpeg 合成。
2. **publish-dispatcher**（端口 8005）—— 当前部分桩实现（真实内容安全但伪发布），实现真实平台 HTTP 调用 + Vault OAuth + `publish_log` 持久化。
3. **asset-manager**（端口 8006）—— 当前最完整但 `/adapt` 路由硬编码平台尺寸，修复为读取 `platform_config` 表。

整体目标：让 `compose_video → publish_to_platforms` 链路在真实云依赖（FFmpeg/MinIO/MySQL/平台 API/Vault）下端到端可运行。

---

## 当前状态分析

| ID | 服务 | 缺陷 | 文件:行 | 严重度 |
|----|------|------|---------|--------|
| D1 | video_composer | 任务全桩：伪进度 + 硬编码 `minio://...output.mp4`，无 FFmpeg | `video_composer/tasks.py:8-33` | 阻断 |
| D2 | video_composer | lifespan 空，无 MySQL/MinIO 初始化 | `video_composer/main.py:12-14` | 高 |
| D3 | video_composer | Redis URL 硬编码 `redis://:dev_redis_2024@localhost:6379/0` | `video_composer/routes.py:15-16` | 中 |
| D4 | video_composer | 状态字段名 `progress`（其他服务用 `progress_percent`） | `video_composer/routes.py:35`、`tasks.py:14-18` | 中 |
| D5 | video_composer | 输出契约不一致：返回 `minio://bucket/obj` 字符串，与 ai_generation 的 `clip_objects: list[str]`（裸对象名）不齐 | `video_composer/tasks.py:20,23` | 中 |
| D6 | video_composer | 不下载 MinIO 片段、不 concat、不混音、不烧字幕、不更新 `generation_pipelines` | `video_composer/tasks.py` 整体 | 阻断 |
| D7 | 全局 | 无共享 `ffmpeg_helper`，asset_manager 重复 subprocess 代码 | `asset_manager/tasks.py:37-49` | 中 |
| D8 | video_composer | config.py 硬编码 JWT_SECRET，未用 `common_sdk.config_manager`，无 MySQL/Redis/MinIO 配置 | `video_composer/config.py:1-5` | 中 |
| D9 | publish_dispatcher | 任务伪发布：`platform_post_id=f"post_{platform}_{pipeline_id}"`、`public_url=f"https://{platform}.com/post/..."` | `publish_dispatcher/tasks.py:23-24` | 阻断 |
| D10 | publish_dispatcher | 无 `publish_log` 表 INSERT，路由 `GET /publish/{task_id}` 永远 404 | `publish_dispatcher/tasks.py` 整体、`routes.py:46-63` | 阻断 |
| D11 | publish_dispatcher | 无 Vault OAuth 刷新（`vault_client.get_platform_refresh_token` 已存在但未被调用） | `publish_dispatcher/tasks.py` | 高 |
| D12 | publish_dispatcher | 无真实平台 API 调用，`BasePlatformPublisher` 零子类 | `utils/platform_connectors/` | 阻断 |
| D13 | publish_dispatcher | 不调用 asset-manager `/adapt`（架构 L509 要求先适配再发布） | `publish_dispatcher/tasks.py` | 中 |
| D14 | publish_dispatcher | Redis URL 硬编码 | `publish_dispatcher/tasks.py:11,34` | 中 |
| D15 | asset_manager | `/adapt` 路由硬编码 `width/height/max_duration`，未读 `platform_config` 表 | `asset_manager/routes.py:27-33` | 中 |
| D16 | asset_manager | tasks.py 用 `-an` 丢弃音频，应保留原音轨 | `asset_manager/tasks.py:39` | 低 |
| D17 | 全局 | video_composer / publish_dispatcher / asset_manager 零单元测试 | `tests/` | 中 |

---

## 设计决策

### 决策 1：统一 MinIO 对象引用契约

**采用"裸对象名"作为内部传递标准**（与 ai_generation 对齐）：
- 任务返回值：`{"output_object": "final/{tenant}/{pipeline_id}/output.mp4"}`、`{"clip_objects": [...]}`、`{"image_objects": [...]}`
- 不带 `minio://` 前缀，不带 bucket 名
- 需要访问时通过 `_minio_helper.presigned_url(obj)` 或 `MinioClient.download_file(bucket, obj, path)` 现取
- routes 层返回给外部时按需转 presigned URL

### 决策 2：提取共享 `utils/ffmpeg_helper.py`

封装 4 个原子函数（均同步，内部 `subprocess.run`），所有调用方在 Celery 任务中直接同步调用：
- `concat_clips(clip_paths: list[Path], output_path: Path) -> None` —— 使用 `concat` demuxer
- `mux_audio(video_path: Path, audio_path: Path, output_path: Path) -> None` —— `-c copy -map 0:v -map 1:a`
- `burn_subtitle(video_path: Path, subtitle_text: str, output_path: Path, style: dict = None) -> None` —— 生成临时 `.srt` 后用 `subtitles` 滤镜
- `transcode_scale(input_path: Path, output_path: Path, width: int, height: int, max_duration: int = 0, drop_audio: bool = False) -> tuple[Path, Path]` —— 返回 `(output_path, cover_path)`，供 asset_manager 复用

### 决策 3：video_composer 真实合成流程

`compose_video_task` 真实步骤：
1. 从 `video_clips`（裸对象名列表）逐个 `minio.download_file` 到本地临时目录
2. 调 `ffmpeg_helper.concat_clips` 拼接为 `concat.mp4`
3. 若 `audio_url` 非空：下载音频 → `mux_audio(concat.mp4, audio.mp3, with_audio.mp4)`
4. 若 `subtitle_text` 非空：`burn_subtitle(with_audio.mp4, subtitle_text, with_sub.mp4)`
5. 上传最终文件到 `final/{tenant}/{pipeline_id}/output.mp4`
6. `mysql.execute("UPDATE generation_pipelines SET final_video_url=?, compose_status='completed' WHERE id=?")`（fail-soft try/except）
7. Redis `task:{id}` 写 `status=completed`、`progress_percent=100`、`result={"output_object": "..."}`
8. 临时目录清理（finally）

### 决策 4：publish_dispatcher 真实发布架构

**新增 `utils/platform_connectors/douyin_publisher.py`** —— 真实抖音开放平台客户端：
- `upload_video`：POST `https://open.douyin.com/api/douyin/v1/video/upload_video/`（分块上传）
- `create_video`：POST `https://open.douyin.com/api/douyin/v1/video/create_video/`
- `refresh_token`：POST `https://open.douyin.com/oauth/renew_refresh_token/`
- 所有 HTTP 调用通过 `httpx`，失败抛 `PlatformException`

**新增 `utils/platform_connectors/generic_http_publisher.py`** —— 通用 HTTP 发布器：
- 从 `platform_config` 表读 `api_upload_url`、`api_publish_url`、`api_token_url`
- 通用 multipart 上传 + JSON publish
- 用于未实现专用 publisher 的平台（如 youtube/tiktok 由运营在 platform_config 配置端点）

**`BasePlatformPublisher.get_oauth_token` 真实实现**：调用 `vault_client.get_platform_refresh_token(platform, tenant)` → 若过期调 `refresh_token_if_needed` → 返回 access_token。

**`publish_to_platform_task` 真实流程**：
1. 内容安全前置检查（已有）
2. 调 asset-manager `POST /api/v1/assets/adapt`（httpx 真实内部 HTTP 调用，带 service JWT）→ 拿 adapted video + cover URL
3. 从 `PublisherRegistry` 取 publisher 实例（worker 单例）
4. `await publisher.publish(PublishRequest(...))` → `PublishResult`
5. `mysql.execute("INSERT INTO publish_log (...) VALUES (...)")` 真实落库
6. `mysql.execute("UPDATE generation_pipelines SET publish_log_id=?, publish_status='completed' WHERE id=?")`（fail-soft）
7. Redis 写最终状态

**Worker 单例模式**：新建 `publish_dispatcher/worker_publishers.py`，模块级初始化 `PublisherRegistry` 并注册 douyin + generic_http，导出 `worker_publishers`。Celery worker 不跑 lifespan，需独立单例（参考 ai_generation/worker_router.py 模式）。

### 决策 5：asset_manager 修复

`/adapt` 路由改为：
1. 从 `platform_config` 表查 `platform=? AND config_key IN ('width','height','max_duration')`
2. 缺失则回退到当前硬编码默认值（fail-soft）
3. tasks.py 移除 `-an`（除非显式 drop_audio 参数）

### 决策 6：DB 配置统一

video_composer/config.py 改用 `common_sdk.config_manager`（参考 ai_generation/config.py 模式），导出 MYSQL/REDIS/MINIO/CELERY 全套配置。routes.py 改用配置中的 Redis URL。

---

## 实施步骤

### Step 11：video_composer 真实 FFmpeg 合成

**11.1 新建 `utils/ffmpeg_helper.py`**

```python
"""共享 FFmpeg 封装。所有函数同步阻塞，在 Celery 任务中调用。"""
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(cmd, timeout=timeout, capture_output=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[-500:]}")


def concat_clips(clip_paths: list[Path], output_path: Path) -> None:
    if len(clip_paths) == 1:
        # 单片直接 copy
        _run(["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(output_path)])
        return
    list_path = output_path.parent / "concat_list.txt"
    list_path.write_text(
        "\n".join(f"file '{p.absolute()}'" for p in clip_paths), encoding="utf-8"
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
    video_path: Path, subtitle_text: str, output_path: Path,
    style: Optional[dict] = None,
) -> None:
    style = style or {"FontSize": 24, "PrimaryColour": "&Hffffff&", "Outline": 2}
    srt_path = output_path.parent / "subtitle.srt"
    # 简单 SRT：单条全覆盖字幕
    srt_content = "1\n00:00:00,000 --> 99:59:59,000\n" + subtitle_text.replace("\n", "\n") + "\n"
    srt_path.write_text(srt_content, encoding="utf-8")
    style_str = ",".join(f"{k}={v}" for k, v in style.items())
    filter_str = f"subtitles='{srt_path.absolute()}':force_style='{style_str}'"
    _run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", filter_str, "-c:a", "copy", str(output_path),
    ])


def transcode_scale(
    input_path: Path, output_path: Path, width: int, height: int,
    max_duration: int = 0, drop_audio: bool = False,
) -> Path:
    cover_path = output_path.with_suffix(".jpg")
    scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vf", scale_filter]
    if max_duration > 0:
        cmd += ["-t", str(max_duration)]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    if drop_audio:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac"]
    cmd.append(str(output_path))
    _run(cmd)
    _run(["ffmpeg", "-y", "-i", str(output_path), "-vframes", "1", "-q:v", "2", str(cover_path)])
    return cover_path
```

**11.2 重写 `project/backend/video_composer/config.py`** —— 仿 ai_generation/config.py，补齐 MySQL/Redis/MinIO/Celery 配置（用 `common_sdk.config_manager`）。

**11.3 修改 `project/backend/video_composer/main.py`** —— lifespan 增加 MySQL pool + MinIO connect（参考 publish_dispatcher/main.py）。

**11.4 修改 `project/backend/video_composer/routes.py`** —— Redis URL 改用 config；`/compose` 入队时把 `tenant_id` 传入任务参数；字段名 `progress` → `progress_percent`。

**11.5 重写 `project/backend/video_composer/tasks.py`**：

```python
import asyncio
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from utils.mq_clients.celery_app import create_task, BaseTask
from utils.db_clients.minio import get_minio_client
from utils.db_clients.mysql import get_mysql_client
from utils.common_sdk.logger import get_logger
from utils.ffmpeg_helper import concat_clips, mux_audio, burn_subtitle

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
        asyncio.run(mysql.execute(
            f"UPDATE generation_pipelines SET {cols}, updated_at=NOW() WHERE id=%s",
            tuple(params),
        ))
    except Exception as e:
        logger.warning("update_pipeline_failed", pipeline_id=pipeline_id, error=str(e))


@create_task("compose_video", queue="compose_queue")
def compose_video_task(self, task_id, pipeline_id, video_clips, images,
                       audio_url, subtitle_text, template_id, config,
                       tenant_id="default"):
    _set_status(self, task_id, status="running", progress_percent="5")
    temp_dir = Path(tempfile.gettempdir()) / f"compose_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 1. 下载所有片段
        clip_paths = []
        for i, obj in enumerate(video_clips):
            p = temp_dir / f"clip_{i:03d}.mp4"
            _download_object(obj, p)
            clip_paths.append(p)
        _set_status(self, task_id, progress_percent="30")

        # 2. concat
        concat_path = temp_dir / "concat.mp4"
        concat_clips(clip_paths, concat_path)
        current_path = concat_path
        _set_status(self, task_id, progress_percent="55")

        # 3. 音频混合
        if audio_url:
            audio_path = temp_dir / "audio.mp3"
            _download_object(audio_url, audio_path)
            muxed_path = temp_dir / "with_audio.mp4"
            mux_audio(current_path, audio_path, muxed_path)
            current_path = muxed_path
        _set_status(self, task_id, progress_percent="70")

        # 4. 字幕压制
        if subtitle_text:
            sub_path = temp_dir / "with_sub.mp4"
            burn_subtitle(current_path, subtitle_text, sub_path)
            current_path = sub_path
        _set_status(self, task_id, progress_percent="85")

        # 5. 上传 MinIO
        output_obj = f"final/{tenant_id}/{pipeline_id}/output.mp4"
        minio = get_minio_client()
        minio.upload_file(MINIO_BUCKET, output_obj, str(current_path), "video/mp4")

        # 6. 更新数据库（fail-soft）
        _update_pipeline(
            pipeline_id,
            final_video_url=f"{MINIO_BUCKET}/{output_obj}",
            compose_status="completed",
        )

        result = {"output_object": f"{MINIO_BUCKET}/{output_obj}"}
        _set_status(
            self, task_id, status="completed", progress_percent="100",
            result=json.dumps(result, ensure_ascii=False),
        )
        return result
    except Exception as e:
        _set_status(self, task_id, status="failed", error=str(e))
        _update_pipeline(pipeline_id, compose_status="failed", error_message=str(e)[:500])
        raise
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
```

**11.6 重构 `project/backend/asset_manager/tasks.py`** —— 改用 `ffmpeg_helper.transcode_scale`，移除内联 subprocess 代码；`-an` 改为根据 `drop_audio` 参数（默认 False，保留音频）。

**11.7 修改 `project/backend/asset_manager/routes.py` `/adapt`** —— 调用前先查 `platform_config` 表：

```python
async def _get_platform_dims(platform: str) -> tuple[int, int, int]:
    defaults = {"youtube": (1920, 1080, 0), "tiktok": (1080, 1920, 60),
                "instagram": (1080, 1080, 60)}
    try:
        mysql = get_mysql_client()
        rows = await mysql.fetchall(
            "SELECT config_key, config_value FROM platform_config WHERE platform=%s",
            (platform,),
        )
        cfg = {r["config_key"]: r["config_value"] for r in rows}
        return (
            int(cfg.get("width", defaults.get(platform, (1080, 1920, 60))[0])),
            int(cfg.get("height", defaults.get(platform, (1080, 1920, 60))[1])),
            int(cfg.get("max_duration", defaults.get(platform, (1080, 1920, 60))[2])),
        )
    except Exception:
        return defaults.get(platform, (1080, 1920, 60))
```

---

### Step 12：publish_dispatcher 真实发布 + DB 持久化

**12.1 新建 `utils/platform_connectors/generic_http_publisher.py`**：

```python
from __future__ import annotations
import httpx
from .base_publisher import BasePlatformPublisher
from .models import PublishRequest, PublishResult


class GenericHTTPPublisher(BasePlatformPublisher):
    """通用 HTTP 发布器，端点配置在 platform_config 表。"""

    async def publish(self, request: PublishRequest) -> PublishResult:
        cfg = self.platform_config.config
        token = await self.get_oauth_token()
        async with httpx.AsyncClient(timeout=300) as client:
            # 1. 上传视频
            upload_resp = await client.post(
                cfg["api_upload_url"],
                headers={"Authorization": f"Bearer {token}"},
                json={"video_url": request.content.video_url,
                      "cover_url": request.content.cover_url},
            )
            upload_resp.raise_for_status()
            platform_video_id = upload_resp.json().get("video_id", "")

            # 2. 创建发布
            publish_resp = await client.post(
                cfg["api_publish_url"],
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "video_id": platform_video_id,
                    "title": request.content.title,
                    "description": request.content.description,
                    "tags": request.content.tags,
                },
            )
            publish_resp.raise_for_status()
            data = publish_resp.json()
            return PublishResult(
                platform_post_id=data.get("post_id", ""),
                public_url=data.get("public_url", ""),
                status="published",
            )

    async def refresh_token_if_needed(self) -> None:
        # 由 get_oauth_token 触发，此处可加过期检查逻辑
        pass
```

**12.2 修改 `utils/platform_connectors/base_publisher.py` `get_oauth_token`** —— 真实调用 Vault：

```python
async def get_oauth_token(self) -> str:
    from common_sdk.vault_client import vault_client
    tenant = self.platform_config.config.get("tenant_id", "default")
    refresh_token = await vault_client.get_platform_refresh_token(self.platform_id, tenant)
    if not refresh_token:
        return os.environ.get(f"{self.platform_id.upper()}_ACCESS_TOKEN", "")
    # 简化：直接返回 refresh_token；生产中应调用 refresh_token_if_needed 换 access_token
    return refresh_token
```

**12.3 新建 `project/backend/publish_dispatcher/worker_publishers.py`** —— worker 单例：

```python
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from common_sdk.logger import get_logger
from platform_connectors import PublisherRegistry, PlatformAdapterConfig
from platform_connectors.generic_http_publisher import GenericHTTPPublisher

logger = get_logger(__name__)

reg = PublisherRegistry()
reg.register_publisher("generic", GenericHTTPPublisher)
# 配置示例：运营在 platform_config 表为每个平台填 api_upload_url/api_publish_url
reg.load_from_config([
    PlatformAdapterConfig(platform_id="youtube", connector_class="GenericHTTPPublisher",
                          config={"tenant_id": "default"}),
    PlatformAdapterConfig(platform_id="tiktok", connector_class="GenericHTTPPublisher",
                          config={"tenant_id": "default"}),
    PlatformAdapterConfig(platform_id="instagram", connector_class="GenericHTTPPublisher",
                          config={"tenant_id": "default"}),
])
worker_publishers = reg
logger.info("worker_publishers_initialized",
            platforms=reg.list_platforms())
```

**12.4 重写 `project/backend/publish_dispatcher/tasks.py`**：

```python
import asyncio
import json
import os

from utils.mq_clients.celery_app import create_task
from utils.common_sdk.content_safety import content_safety_client
from utils.common_sdk.exceptions import ContentFilteredException
from utils.common_sdk.logger import get_logger
from utils.db_clients.mysql import get_mysql_client
from utils.platform_connectors import PublishRequest, PublishContent

from .worker_publishers import worker_publishers
from .config import ASSET_MANAGER_URL, JWT_SECRET, SERVICE_NAME

logger = get_logger(__name__)


def _set_status(task, task_id, **fields):
    task.redis_client.hset(f"task:{task_id}", mapping=fields)
    task.redis_client.expire(f"task:{task_id}", 86400)


def _insert_publish_log(pipeline_id, platform, result, tenant_id, title, description, tags):
    try:
        mysql = get_mysql_client()
        async def _do():
            await mysql.execute(
                "INSERT INTO publish_log (tenant_id, pipeline_id, platform, "
                "platform_post_id, public_url, status, publish_content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tenant_id, pipeline_id, platform, result.platform_post_id,
                 result.public_url, result.status,
                 json.dumps({"title": title, "description": description, "tags": tags},
                            ensure_ascii=False)),
            )
            row = await mysql.fetchone("SELECT LAST_INSERT_ID() AS id")
            return row["id"] if row else None
        return asyncio.run(_do())
    except Exception as e:
        logger.warning("insert_publish_log_failed", error=str(e))
        return None


def _adapt_video(video_url, platform, tenant_id):
    """调 asset-manager /adapt 获取适配后 URL（fail-soft）。"""
    try:
        import httpx
        from common_sdk.auth import create_service_jwt
        token = create_service_jwt(SERVICE_NAME, JWT_SECRET)
        async def _do():
            async with httpx.AsyncClient(timeout=300) as c:
                resp = await c.post(
                    f"{ASSET_MANAGER_URL}/api/v1/assets/adapt",
                    headers={"Authorization": f"Bearer {token}",
                             "X-Tenant-ID": tenant_id},
                    json={"video_url": video_url, "platforms": [platform]},
                )
                resp.raise_for_status()
                return resp.json()
        return asyncio.run(_do())
    except Exception as e:
        logger.warning("adapt_video_failed", error=str(e))
        return None


@create_task("publish_to_platform", queue="publish_queue")
def publish_to_platform_task(self, task_id, pipeline_id, platform, video_url,
                             title, description, tags, scheduled_time, tenant_id):
    _set_status(self, task_id, status="running", progress_percent="10")
    try:
        # 1. 内容安全
        safety = asyncio.run(content_safety_client.check_video_async(video_url))
        if not safety.passed:
            _set_status(self, task_id, status="content_filtered",
                        error=f"content filtered: {safety.detail}")
            raise ContentFilteredException(message=safety.detail)
        _set_status(self, task_id, progress_percent="30")

        # 2. 适配（fail-soft，失败则用原 URL）
        adapted = _adapt_video(video_url, platform, tenant_id)
        final_video_url = video_url
        cover_url = None
        if adapted and adapted.get("data", {}).get("task_id"):
            # 适配是异步任务，此处简化：直接用原 URL（生产中应轮询）
            logger.info("adapt_dispatched", platform=platform)
        _set_status(self, task_id, progress_percent="50")

        # 3. 真实发布
        publisher = worker_publishers.get_publisher(platform)
        if publisher is None:
            raise RuntimeError(f"No publisher registered for platform={platform}")
        req = PublishRequest(
            platform=platform,
            content=PublishContent(
                video_url=final_video_url, cover_url=cover_url,
                title=title, description=description or "", tags=tags or [],
            ),
            platform_config={"tenant_id": tenant_id},
        )
        result = asyncio.run(publisher.publish(req))
        _set_status(self, task_id, progress_percent="85")

        # 4. 落库
        log_id = _insert_publish_log(pipeline_id, platform, result, tenant_id,
                                     title, description, tags)
        try:
            mysql = get_mysql_client()
            asyncio.run(mysql.execute(
                "UPDATE generation_pipelines SET publish_log_id=%s, "
                "publish_status='completed' WHERE id=%s",
                (log_id, pipeline_id),
            ))
        except Exception as e:
            logger.warning("update_pipeline_publish_failed", error=str(e))

        result_data = {
            "platform_post_id": result.platform_post_id,
            "public_url": result.public_url,
            "platform": platform,
            "publish_log_id": log_id,
        }
        _set_status(self, task_id, status="completed", progress_percent="100",
                    result=json.dumps(result_data, ensure_ascii=False))
        return result_data
    except Exception as e:
        _set_status(self, task_id, status="failed", error=str(e))
        # 失败也落库
        try:
            mysql = get_mysql_client()
            asyncio.run(mysql.execute(
                "INSERT INTO publish_log (tenant_id, pipeline_id, platform, status, "
                "error_message) VALUES (%s, %s, %s, 'failed', %s)",
                (tenant_id, pipeline_id, platform, str(e)[:500]),
            ))
        except Exception:
            pass
        raise
```

**12.5 修改 `project/backend/publish_dispatcher/config.py`** —— 增加 `ASSET_MANAGER_URL`、`JWT_SECRET`、`SERVICE_NAME`（用 `common_sdk.config_manager`）。

**12.6 修改 `project/backend/publish_dispatcher/routes.py` `/publish`** —— `send_task` 参数增加 `tenant_id`（已传）；字段名 `progress` → `progress_percent`。

---

### Step 13：单元测试

**13.1 新建 `tests/test_video_composer_phase3.py`**（5 测试）：

1. `test_ffmpeg_helper_concat_single_clip` —— 单片 concat 走 `-c copy` 分支，mock subprocess.run 断言命令
2. `test_ffmpeg_helper_concat_multiple_clips` —— 多片生成 concat_list.txt 并调 `-f concat`
3. `test_ffmpeg_helper_burn_subtitle_generates_srt` —— 断言 srt 文件生成 + subtitles 滤镜
4. `test_compose_video_task_downloads_and_uploads` —— mock MinIO + ffmpeg_helper + MySQL，调 `compose_video_task.run(...)`，断言 `output_object` 形如 `prodvideofactory/final/.../output.mp4`、`_update_pipeline` 被调用
5. `test_compose_video_task_updates_pipeline_failed_on_error` —— mock ffmpeg 抛错，断言 `compose_status=failed` 写入

**13.2 新建 `tests/test_publish_dispatcher_phase3.py`**（5 测试）：

1. `test_worker_publishers_singleton_has_generic` —— import worker_publishers，断言 `get_publisher("youtube")` 返回 GenericHTTPPublisher 实例
2. `test_generic_http_publisher_publish_calls_upload_and_publish` —— mock httpx.AsyncClient，断言两次 POST
3. `test_publish_to_platform_task_inserts_publish_log` —— mock publisher 返回 PublishResult + mock MySQL，断言 INSERT INTO publish_log 被调用
4. `test_publish_to_platform_task_handles_content_filtered` —— mock content_safety 返回 not passed，断言 ContentFilteredException
5. `test_publish_to_platform_task_fails_log_inserted` —— mock publisher 抛错，断言失败 publish_log INSERT

**13.3 新建 `tests/test_asset_manager_phase3.py`**（2 测试）：

1. `test_adapt_route_reads_platform_config` —— mock MySQL fetchall 返回 width/height/max_duration，断言传入任务的参数与配置一致
2. `test_adapt_route_falls_back_to_defaults` —— mock MySQL 抛错，断言回退到默认值

**测试约定**：`sys.path.insert` 注入 utils/ 和 project root；`pytest` + `pytest-asyncio`；mock 所有外部 IO（subprocess/MinIO/MySQL/httpx/Redis）；`@pytest.mark.asyncio` 标注异步测试；直接调用路由函数或 task `.run(...)`，不走完整 HTTP。参考 `tests/test_ai_generation_phase2.py` 风格。

---

## 假设与决定

1. **平台发布器范围**：本阶段只实现 `GenericHTTPPublisher`（配置驱动）作为通用真实发布器，**不**为抖音/YouTube 各写专用 OAuth 流程（那需要真实 App 凭证和平台 App 注册，超出代码层面）。专用 publisher（如 `DouyinPublisher`）留待运营拿到真实凭证后按相同模式扩展。`BasePlatformPublisher.get_oauth_token` 真实调 Vault，但 token 刷新的 platform-specific HTTP 暂保留为 pass（注释 TODO）。
2. **asset-manager 适配异步等待**：`publish_to_platform_task` 调 `/adapt` 后**不**轮询 adapt 任务完成（adapt 是异步任务），简化为直接用原 video_url 发布。生产中应改为 chord/chain 或轮询。本阶段在代码注释中标明。
3. **FFmpeg 可用性**：假设执行环境已安装 `ffmpeg` 二进制并在 PATH。`asset_manager` 已依赖此前提。
4. **MinIO 对象引用契约**：内部任务返回值统一用 `{bucket}/{object_name}` 形式（如 `prodvideofactory/final/.../output.mp4`），routes 层按需转 presigned URL。这与 ai_generation 的 `clip_objects` 裸对象名（无 bucket）略有差异，但 video_composer 的输出是最终产物，带 bucket 便于外部直接消费；adapter 内部生成的中间产物保持裸对象名。
5. **`generation_pipelines.id` 类型**：SQL schema 是 BIGINT，但代码中 pipeline_id 一直按字符串传递。本计划保持字符串传递，MySQL 隐式转换。
6. **失败 publish_log 也落库**：publish_to_platform_task 失败时 INSERT 一条 `status=failed` 的 publish_log，便于运营审计。
7. **测试不依赖真实 ffmpeg/MinIO/MySQL**：全部 mock，仅验证调用契约。

---

## 验证步骤

1. **语法检查**：`python -c "import ast; ast.parse(open('文件路径').read())"` 对每个修改文件
2. **单元测试**：`python -m pytest tests/test_video_composer_phase3.py tests/test_publish_dispatcher_phase3.py tests/test_asset_manager_phase3.py -v` 全绿
3. **回归**：`python -m pytest tests/ -v` 全部测试（含 Phase 2 的 19 个）仍全绿
4. **Grep 验证**：
   - `grep -rn "redis://:dev_redis_2024@localhost:6379/0" project/backend/video_composer/ project/backend/publish_dispatcher/` 应无结果（已迁移到 config）
   - `grep -rn "post_{platform}_{pipeline_id}" project/backend/publish_dispatcher/` 应无结果（伪发布已移除）
   - `grep -rn "minio://prodvideofactory/final/default" project/backend/video_composer/` 应无结果（硬编码已移除）
   - `grep -rn "subprocess.run.*ffmpeg" project/backend/asset_manager/tasks.py` 应无结果（已迁移到 ffmpeg_helper）
5. **import 验证**：`python -c "from project.backend.video_composer.tasks import compose_video_task; from project.backend.publish_dispatcher.tasks import publish_to_platform_task; from utils.ffmpeg_helper import concat_clips, mux_audio, burn_subtitle, transcode_scale; print('imports ok')"` 成功

---

## 文件清单

### 新建（4 个）
- `utils/ffmpeg_helper.py` —— 共享 FFmpeg 封装
- `utils/platform_connectors/generic_http_publisher.py` —— 通用 HTTP 发布器
- `project/backend/publish_dispatcher/worker_publishers.py` —— worker 单例
- `tests/test_video_composer_phase3.py`、`tests/test_publish_dispatcher_phase3.py`、`tests/test_asset_manager_phase3.py` —— 12 个单元测试

### 修改（8 个）
- `project/backend/video_composer/config.py` —— 补齐配置
- `project/backend/video_composer/main.py` —— lifespan 初始化
- `project/backend/video_composer/routes.py` —— Redis URL/字段名/tenant_id 透传
- `project/backend/video_composer/tasks.py` —— 真实 FFmpeg 合成（核心重写）
- `project/backend/publish_dispatcher/config.py` —— 增加 ASSET_MANAGER_URL 等
- `project/backend/publish_dispatcher/tasks.py` —— 真实发布 + publish_log 落库（核心重写）
- `project/backend/publish_dispatcher/routes.py` —— 字段名/tenant_id 透传
- `project/backend/asset_manager/routes.py` —— `/adapt` 读 platform_config 表
- `project/backend/asset_manager/tasks.py` —— 用 ffmpeg_helper + 保留音频
- `utils/platform_connectors/base_publisher.py` —— `get_oauth_token` 真实调 Vault

### 不动
- ai_generation/* （Phase 2 已完成）
- common_sdk/* （现有 vault_client/content_safety 已够用）
- mcp-gateway / crawl-scheduler / product-analyzer / web-backend（不在本阶段范围）
