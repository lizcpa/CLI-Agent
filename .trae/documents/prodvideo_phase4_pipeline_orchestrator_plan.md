# Phase 4: Pipeline DAG 编排器 + 基础设施 Bug 修复

## 摘要

本计划完成两件事：
1. **修复 Phase 3 遗留的 9 个失败测试**（3 个根因）
2. **实现 Pipeline DAG 编排器** — 架构文档 §5.1-5.3（L565-629）的核心缺口：8 个微服务目前互不相连，无法端到端跑通"采集→分析→生成→合成→发布"流水线

实现完成后，系统将支持：
- 手动触发完整流水线（HTTP `POST /api/v1/pipelines`）
- 事件驱动自动触发（product-analyzer 发布 `product:hot_score_changed` → 编排器自动创建流水线）
- 每步更新 `generation_pipelines` 表的 `stage` 字段

---

## 当前状态分析

### Phase 3 测试失败根因（3 类）

| 编号 | 失败测试 | 根因 | 影响范围 |
|------|---------|------|---------|
| R1 | `test_ffmpeg_helper_concat_single_clip` 等 3 个 | `mock_run.return_value.returncode` 是 MagicMock（truthy），`_run` 进入错误分支抛 `FFmpegError` | ffmpeg_helper 3 个测试 |
| R2 | `test_worker_publishers_singleton_has_generic` | `worker_publishers.py` 只注册了 `"generic"` 平台，`load_from_config` 只设配置不注册类，`get_publisher("youtube")` 返回 None | 1 个测试 |
| R3 | `test_compose_video_task_*` 等 5 个 | **双模块导入问题**：tasks.py 用 `from utils.mq_clients.celery_app import BaseTask`，测试用 `from mq_clients.celery_app import BaseTask` → 两个不同的 `BaseTask` 类 → PropertyMock 打补丁在错误的类上 | video_composer 2 个 + publish_dispatcher 3 个测试 |

### 架构文档缺口

| 编号 | 缺口 | 架构位置 | 当前状态 |
|------|------|---------|---------|
| G1 | Pipeline DAG 编排器 | §5.1 L565-617 | 完全缺失，8 个服务互不相连 |
| G2 | 事件驱动自动触发 | §5.3 L627-629 | product-analyzer 已发布事件，无监听者 |
| G3 | crawl_scheduler 硬编码 Redis URL | 基础设施 Bug | `tasks.py` L16, L55 硬编码 `redis://:dev_redis_2024@localhost:6379/0` |
| G4 | mcp_gateway 硬编码 Redis URL | 基础设施 Bug | `tool_handlers.py` L132 硬编码同上 |

### 现有任务签名（DAG 编排器需要调用）

| 服务 | 任务/端点 | 签名 |
|------|----------|------|
| product-analyzer | `POST /api/v1/analyze` | `{product_ids, platform, limit}` → `{analyzed_count, hot_count}` |
| ai-generation | `POST /api/v1/copywriting` | `{product_id, product_title, product_desc, keywords, style, max_length, model}` → `{text}` |
| ai-generation | `POST /api/v1/images/generate` | `{prompts, size, n, model}` → `{image_objects}` |
| ai-generation | `POST /api/v1/videos/generate` | `{type, prompts, reference_image_url, duration, resolution, count, model}` → `{clip_objects}` |
| video-composer | `POST /api/v1/compose` | `{pipeline_id, video_clips, images, audio_url, subtitle_text, template_id, config}` → `{output_object}` |
| publish-dispatcher | `POST /api/v1/publish` | `{pipeline_id, video_url, platforms, title, description, tags, scheduled_time}` → `{platform_post_id, public_url, publish_log_id}` |

### generation_pipelines 表结构（已存在）

```sql
id BIGINT PK, tenant_id, product_id, 
stage ENUM('pending','crawling','analyzing','generating','composing','publishing','completed','failed','content_filtered'),
copywriting TEXT, copywriting_status ENUM(...),
image_urls JSON, images_status ENUM(...),
video_clip_urls JSON, video_clips_status ENUM(...),
final_video_url VARCHAR(1024), compose_status ENUM(...),
publish_log_id BIGINT, publish_status ENUM(...),
config JSON, error_message TEXT, created_at, updated_at
```

---

## 设计决策

### D1: 编排器作为独立微服务 `pipeline_orchestrator`（端口 8008）

**理由**：架构文档 §5.3 说"ai-generation 监听该频道"，但编排逻辑跨所有服务。独立服务职责清晰，避免污染 ai-generation。8 服务 → 9 服务。

### D2: 使用 HTTP 调用（httpx）而非 Celery Canvas

**理由**：
- Celery Canvas 跨服务 chord/chain 需要所有 worker 加载所有任务模块，耦合度高
- HTTP 调用模式与 mcp_gateway/tool_handlers.py 一致，已有 `_post`/`_get` 辅助函数可复用
- 每步 DB 更新更显式，错误处理更简单（try/except per step）
- `asyncio.gather` 实现并行 generate_* 调用

### D3: 编排器任务用 Celery + HTTP 混合模式

- 编排器自身是 Celery 任务（`run_pipeline_task`，队列 `orchestrator_queue`），支持异步执行和重试
- 任务内部用 `httpx.AsyncClient` + `asyncio.run()` 调用各服务 HTTP 端点
- 每步更新 `generation_pipelines` 表

### D4: Redis Pub/Sub 监听器在 FastAPI lifespan 中启动

- `main.py` lifespan 启动后台 `asyncio.Task` 订阅 `product:hot_score_changed`
- 收到消息 → 解析 JSON → 检查 `score >= threshold` → `send_task("pipeline_orchestrator.tasks.run_pipeline_task", ...)`
- lifespan 关闭时取消监听任务

### D5: 修复双模块导入问题 — 移除 `utils.` 前缀

**根因**：`video_composer/tasks.py` 和 `publish_dispatcher/tasks.py` 用 `from utils.mq_clients.celery_app import BaseTask`，但测试用 `from mq_clients.celery_app import BaseTask`。Python 将这两个视为不同模块 → 两个不同的 `BaseTask` 类 → `patch.object(BaseTask, ...)` 打在错误的类上。

**修复**：将 tasks.py 中所有 `from utils.xxx import ...` 改为 `from xxx import ...`（因为 `sys.path.insert(0, .../utils)` 已让 `utils/` 目录可搜索）。这与 `ai_generation/tasks.py` 的模式一致（它用 `from mq_clients.celery_app import ...` 无前缀，测试通过）。

### D6: worker_publishers 为每个平台注册类

`PublisherRegistry.register_publisher(platform_id, class)` 注册类；`load_from_config` 只设配置。修复：为 `youtube`/`tiktok`/`instagram` 各注册一次 `GenericHTTPPublisher`。

### D7: 硬编码 Redis URL 修复 — 使用 `common_sdk.config_manager`

`crawl_scheduler/tasks.py` 和 `mcp_gateway/tool_handlers.py` 改用 `config_manager.get("REDIS_HOST")` 等读取配置，与 Phase 2/3 模式一致。

---

## 实施步骤

### Step 14: 修复 9 个失败测试（3 个根因）

#### 14.1 修复 ffmpeg_helper 测试（3 个失败）

**文件**: `tests/test_video_composer_phase3.py`

**修改**: 在 3 个 ffmpeg_helper 测试的 `with patch("ffmpeg_helper.subprocess.run") as mock_run:` 后添加 `mock_run.return_value.returncode = 0`：

```python
# test_ffmpeg_helper_concat_single_clip (L20-21)
with patch("ffmpeg_helper.subprocess.run") as mock_run:
    mock_run.return_value.returncode = 0
    concat_clips([clip], out)

# test_ffmpeg_helper_concat_multiple_clips (L37-38)
with patch("ffmpeg_helper.subprocess.run") as mock_run:
    mock_run.return_value.returncode = 0
    concat_clips(clips, out)

# test_ffmpeg_helper_burn_subtitle_generates_srt (L53-54)
with patch("ffmpeg_helper.subprocess.run") as mock_run:
    mock_run.return_value.returncode = 0
    burn_subtitle(video, "hello world", out)
```

#### 14.2 修复 worker_publishers 注册（1 个失败）

**文件**: `project/backend/publish_dispatcher/worker_publishers.py`

**修改** (L14-25): 将 `reg.register_publisher("generic", GenericHTTPPublisher)` 改为为每个平台注册：

```python
reg = PublisherRegistry()
for p in _DEFAULT_PLATFORMS:
    reg.register_publisher(p, GenericHTTPPublisher)

reg.load_from_config([
    PlatformAdapterConfig(
        platform_id=p,
        connector_class="GenericHTTPPublisher",
        config={"tenant_id": "default"},
    )
    for p in _DEFAULT_PLATFORMS
])
```

#### 14.3 修复双模块导入问题（5 个失败）

**文件 1**: `project/backend/video_composer/tasks.py` (L11-15)

```python
# 修改前:
from utils.mq_clients.celery_app import create_task, BaseTask
from utils.db_clients.minio import get_minio_client
from utils.db_clients.mysql import get_mysql_client
from utils.common_sdk.logger import get_logger
from utils.ffmpeg_helper import concat_clips, mux_audio, burn_subtitle

# 修改后:
from mq_clients.celery_app import create_task, BaseTask
from db_clients.minio import get_minio_client
from db_clients.mysql import get_mysql_client
from common_sdk.logger import get_logger
from ffmpeg_helper import concat_clips, mux_audio, burn_subtitle
```

**文件 2**: `project/backend/publish_dispatcher/tasks.py` (L8-13)

```python
# 修改前:
from utils.mq_clients.celery_app import create_task, BaseTask
from utils.common_sdk.content_safety import content_safety_client
from utils.common_sdk.exceptions import ContentFilteredException
from utils.common_sdk.logger import get_logger
from utils.db_clients.mysql import get_mysql_client
from utils.platform_connectors import PublishRequest, PublishContent

# 修改后:
from mq_clients.celery_app import create_task, BaseTask
from common_sdk.content_safety import content_safety_client
from common_sdk.exceptions import ContentFilteredException
from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client
from platform_connectors import PublishRequest, PublishContent
```

**验证**: 运行 `python -m pytest tests/test_video_composer_phase3.py tests/test_publish_dispatcher_phase3.py tests/test_asset_manager_phase3.py -v` — 预期 12/12 通过。

---

### Step 15: 修复硬编码 Redis URL（2 个基础设施 Bug）

#### 15.1 修复 crawl_scheduler/tasks.py

**文件**: `project/backend/crawl_scheduler/tasks.py`

**修改**: 移除硬编码 Redis URL，改用 `common_sdk.config_manager`（与 Phase 2 模式一致）。

在文件顶部添加配置读取：
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from common_sdk.config_manager import config_manager
from common_sdk.logger import get_logger

logger = get_logger(__name__)

def _get_redis():
    import redis as sync_redis
    return sync_redis.Redis(
        host=config_manager.get("REDIS_HOST", "localhost"),
        port=int(config_manager.get("REDIS_PORT", "6379")),
        password=config_manager.get("REDIS_PASSWORD", ""),
        db=int(config_manager.get("REDIS_DB", "0")),
        decode_responses=True,
    )
```

替换 L16-18 和 L55-56 的硬编码 Redis 调用为 `r = _get_redis()`。

同时修复 `execute_crawl_job` 缺少 `self` 参数的问题（`@create_task` 用 `bind=True`）：
```python
@create_task("execute_crawl_job", queue="crawl_queue")
def execute_crawl_job(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default"):
```

#### 15.2 修复 mcp_gateway/tool_handlers.py

**文件**: `project/backend/mcp_gateway/tool_handlers.py`

**修改** (L128-152): `handle_query_task_status` 硬编码 Redis URL，改用配置：

```python
async def handle_query_task_status(arguments: dict) -> dict:
    task_id = arguments.get("task_id")
    try:
        import redis.asyncio as aioredis
        from common_sdk.config_manager import config_manager
        r = aioredis.Redis(
            host=config_manager.get("REDIS_HOST", "localhost"),
            port=int(config_manager.get("REDIS_PORT", "6379")),
            password=config_manager.get("REDIS_PASSWORD", ""),
            db=int(config_manager.get("REDIS_DB", "0")),
            decode_responses=True,
        )
        data = await r.hgetall(f"task:{task_id}")
        await r.aclose()
        if data:
            return {"task_id": task_id, "status": data.get("status", "unknown"),
                    "progress": data.get("progress_percent", data.get("progress", 0)),
                    "result": data.get("result")}
    except Exception:
        pass

    # 补充缺失的 ai-generation 服务查询
    for service_path in [
        ("crawl-scheduler", f"/api/v1/crawl/jobs/{task_id}"),
        ("ai-generation", f"/api/v1/tasks/{task_id}"),
        ("video-composer", f"/api/v1/compose/{task_id}"),
        ("publish-dispatcher", f"/api/v1/publish/{task_id}"),
    ]:
        try:
            svc, path = service_path
            result = await _get(svc, path)
            return {"task_id": task_id, **result.get("data", result)}
        except Exception:
            continue

    return {"task_id": task_id, "status": "not_found", "error": "Task not found"}
```

注意：`redis.asyncio` 的 close 方法在新版本是 `aclose()`，旧版本是 `close()` — 用 try/except 兼容。

---

### Step 16: 实现 Pipeline DAG 编排器

#### 16.1 创建 pipeline_orchestrator 包结构

**新建文件**:
```
project/backend/pipeline_orchestrator/
├── __init__.py
├── config.py
├── main.py
├── routes.py
├── tasks.py
└── subscriber.py
```

#### 16.2 config.py

```python
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import os
from common_sdk.config_manager import config_manager

SERVICE_NAME = "pipeline-orchestrator"
SERVICE_PORT = 8008
JWT_SECRET = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")

REDIS_HOST = config_manager.get("REDIS_HOST", "localhost")
REDIS_PORT = int(config_manager.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = config_manager.get("REDIS_PASSWORD", "")
REDIS_DB = int(config_manager.get("REDIS_DB", "0"))
REDIS_HOT_SCORE_CHANNEL = "product:hot_score_changed"

MYSQL_HOST = config_manager.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(config_manager.get("MYSQL_PORT", "3306"))
MYSQL_USER = config_manager.get("MYSQL_USER", "dev_user")
MYSQL_PASSWORD = config_manager.get("MYSQL_PASSWORD", "dev_pass_2024")
MYSQL_DATABASE = config_manager.get("MYSQL_DATABASE", "prodvideo")

CELERY_BROKER_URL = config_manager.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = config_manager.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

SCORE_THRESHOLD = float(config_manager.get("PIPELINE_SCORE_THRESHOLD", "70.0"))

SERVICE_ENDPOINTS = {
    "crawl-scheduler": os.getenv("CRAWL_SCHEDULER_URL", "http://localhost:8001"),
    "product-analyzer": os.getenv("PRODUCT_ANALYZER_URL", "http://localhost:8002"),
    "ai-generation": os.getenv("AI_GENERATION_URL", "http://localhost:8003"),
    "video-composer": os.getenv("VIDEO_COMPOSER_URL", "http://localhost:8004"),
    "publish-dispatcher": os.getenv("PUBLISH_DISPATCHER_URL", "http://localhost:8005"),
    "asset-manager": os.getenv("ASSET_MANAGER_URL", "http://localhost:8006"),
}
```

#### 16.3 tasks.py — 核心 DAG 编排任务

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json
import httpx

from mq_clients.celery_app import create_task, BaseTask
from db_clients.mysql import get_mysql_client
from common_sdk.auth import create_service_jwt
from common_sdk.logger import get_logger

from .config import SERVICE_ENDPOINTS, JWT_SECRET, SCORE_THRESHOLD, SERVICE_NAME

logger = get_logger(__name__)


def _set_status(task: BaseTask, task_id: str, **fields) -> None:
    task.redis_client.hset(f"task:{task_id}", mapping=fields)
    task.redis_client.expire(f"task:{task_id}", 86400)


def _get_headers(tenant_id: str = "default") -> dict:
    token = create_service_jwt(SERVICE_NAME, JWT_SECRET)
    return {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": tenant_id,
        "Content-Type": "application/json",
    }


async def _http_post(service: str, path: str, body: dict, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    headers = _get_headers(tenant_id)
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()


async def _http_get(service: str, path: str, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    headers = _get_headers(tenant_id)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _create_pipeline(product_id: int, tenant_id: str) -> int:
    """INSERT INTO generation_pipelines, return new pipeline id."""
    mysql = get_mysql_client()

    async def _do():
        await mysql.execute(
            "INSERT INTO generation_pipelines (tenant_id, product_id, stage) VALUES (%s, %s, 'analyzing')",
            (tenant_id, product_id),
        )
        row = await mysql.fetchone("SELECT LAST_INSERT_ID() AS id")
        return int(row["id"])

    return asyncio.run(_do())


def _update_pipeline(pipeline_id: int, **fields) -> None:
    mysql = get_mysql_client()
    cols = ", ".join(f"{k}=%s" for k in fields)
    params = list(fields.values()) + [pipeline_id]

    async def _do():
        await mysql.execute(
            f"UPDATE generation_pipelines SET {cols}, updated_at=NOW() WHERE id=%s",
            tuple(params),
        )

    asyncio.run(_do())


def _get_product(product_id: int) -> dict:
    mysql = get_mysql_client()

    async def _do():
        await mysql.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        return await mysql.fetchone()

    return asyncio.run(_do())


@create_task("run_pipeline", queue="orchestrator_queue")
def run_pipeline_task(
    self,
    task_id: str,
    product_id: int,
    tenant_id: str = "default",
    config: dict | None = None,
):
    """
    DAG: analyze → parallel(copywriting, images, video_clips) → compose → publish
    """
    config = config or {}
    pipeline_id = None
    _set_status(self, task_id, status="running", progress_percent="5")

    try:
        # 1. Create pipeline record
        pipeline_id = _create_pipeline(product_id, tenant_id)
        _set_status(self, task_id, progress_percent="10", pipeline_id=str(pipeline_id))

        # 2. Analyze product
        _update_pipeline(pipeline_id, stage="analyzing")
        analyze_result = asyncio.run(
            _http_post("product-analyzer", "/api/v1/analyze",
                       {"product_ids": [product_id]}, tenant_id)
        )
        _set_status(self, task_id, progress_percent="20")

        # 3. Get product info for generation
        product = _get_product(product_id)
        if not product:
            raise RuntimeError(f"Product {product_id} not found")

        product_title = product.get("title", "")
        product_desc = product.get("description", "")
        main_image = product.get("main_image_url", "")
        keywords = product.get("tags", []) or []

        # 4. Parallel: copywriting + images + video_clips
        _update_pipeline(pipeline_id, stage="generating")
        _set_status(self, task_id, progress_percent="30")

        async def _run_generation():
            copy_task = _http_post("ai-generation", "/api/v1/copywriting", {
                "product_id": product_id,
                "product_title": product_title,
                "product_desc": product_desc,
                "keywords": keywords,
                "style": config.get("style", "marketing"),
                "max_length": config.get("max_length", 200),
            }, tenant_id)

            img_prompts = [f"{product_title} 产品展示图"]
            img_task = _http_post("ai-generation", "/api/v1/images/generate", {
                "prompts": img_prompts,
                "size": "1024x1024",
                "n": config.get("image_count", 3),
            }, tenant_id)

            vid_prompts = [f"{product_title} 产品宣传视频片段"]
            vid_task = _http_post("ai-generation", "/api/v1/videos/generate", {
                "type": "text2video",
                "prompts": vid_prompts,
                "reference_image_url": main_image,
                "duration": config.get("video_duration", 5),
                "count": config.get("video_count", 2),
            }, tenant_id)

            results = await asyncio.gather(copy_task, img_task, vid_task, return_exceptions=True)
            return results

        gen_results = asyncio.run(_run_generation())

        copy_result = gen_results[0]
        img_result = gen_results[1]
        vid_result = gen_results[2]

        # Check for generation failures (fail-soft: continue if at least video clips succeed)
        copywriting = ""
        if isinstance(copy_result, dict):
            copywriting = copy_result.get("text", "")
            _update_pipeline(pipeline_id, copywriting=copywriting, copywriting_status="completed")
        else:
            logger.warning("copywriting_failed", error=str(copy_result))
            _update_pipeline(pipeline_id, copywriting_status="failed")

        image_urls = []
        if isinstance(img_result, dict):
            image_urls = img_result.get("image_objects", [])
            _update_pipeline(pipeline_id, image_urls=json.dumps(image_urls), images_status="completed")
        else:
            logger.warning("images_failed", error=str(img_result))
            _update_pipeline(pipeline_id, images_status="failed")

        video_clips = []
        if isinstance(vid_result, dict):
            video_clips = vid_result.get("clip_objects", [])
            _update_pipeline(pipeline_id, video_clip_urls=json.dumps(video_clips), video_clips_status="completed")
        else:
            logger.warning("video_clips_failed", error=str(vid_result))
            _update_pipeline(pipeline_id, video_clips_status="failed")

        if not video_clips:
            raise RuntimeError("No video clips generated — cannot compose")

        _set_status(self, task_id, progress_percent="60")

        # 5. Compose video
        _update_pipeline(pipeline_id, stage="composing")
        compose_result = asyncio.run(
            _http_post("video-composer", "/api/v1/compose", {
                "pipeline_id": str(pipeline_id),
                "video_clips": video_clips,
                "images": image_urls,
                "audio_url": None,
                "subtitle_text": copywriting[:200] if copywriting else None,
                "template_id": config.get("template_id"),
                "config": config.get("compose_config"),
            }, tenant_id)
        )
        final_video_url = compose_result.get("output_object") or compose_result.get("output_url", "")
        _set_status(self, task_id, progress_percent="80")

        # 6. Publish
        _update_pipeline(pipeline_id, stage="publishing")
        platforms = config.get("platforms", ["youtube", "tiktok"])
        publish_result = asyncio.run(
            _http_post("publish-dispatcher", "/api/v1/publish", {
                "pipeline_id": str(pipeline_id),
                "video_url": final_video_url,
                "platforms": platforms,
                "title": product_title,
                "description": copywriting or product_desc,
                "tags": keywords,
                "scheduled_time": config.get("scheduled_time"),
            }, tenant_id)
        )

        # 7. Complete
        _update_pipeline(pipeline_id, stage="completed")
        result = {
            "pipeline_id": pipeline_id,
            "final_video_url": final_video_url,
            "publish_result": publish_result,
        }
        _set_status(self, task_id, status="completed", progress_percent="100",
                    result=json.dumps(result, ensure_ascii=False))
        return result

    except Exception as e:
        logger.error("pipeline_failed", product_id=product_id, error=str(e))
        if pipeline_id is not None:
            try:
                _update_pipeline(pipeline_id, stage="failed", error_message=str(e)[:500])
            except Exception:
                pass
        _set_status(self, task_id, status="failed", error=str(e))
        raise
```

**关键**: `pipeline_id = None` 在 try 块之前初始化（见上方），except 中检查 `if pipeline_id is not None` 避免在 pipeline 记录未创建时报错。

#### 16.4 subscriber.py — Redis Pub/Sub 监听器

```python
from __future__ import annotations

import asyncio
import json

from common_sdk.logger import get_logger
from common_sdk.config_manager import config_manager

from .config import REDIS_HOT_SCORE_CHANNEL, SCORE_THRESHOLD

logger = get_logger(__name__)


class HotScoreSubscriber:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._listen())
        logger.info("hot_score_subscriber_started", channel=REDIS_HOT_SCORE_CHANNEL)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("hot_score_subscriber_stopped")

    async def _listen(self):
        import redis.asyncio as aioredis
        r = aioredis.Redis(
            host=config_manager.get("REDIS_HOST", "localhost"),
            port=int(config_manager.get("REDIS_PORT", "6379")),
            password=config_manager.get("REDIS_PASSWORD", ""),
            db=int(config_manager.get("REDIS_DB", "0")),
            decode_responses=True,
        )
        pubsub = r.pubsub()
        await pubsub.subscribe(REDIS_HOT_SCORE_CHANNEL)
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=5.0)
                    if msg and msg["type"] == "message":
                        await self._handle_message(msg["data"])
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("subscriber_error", error=str(e))
                    await asyncio.sleep(1)
        finally:
            await pubsub.unsubscribe(REDIS_HOT_SCORE_CHANNEL)
            await r.aclose()

    async def _handle_message(self, raw: str) -> None:
        try:
            event = json.loads(raw)
            product_id = event.get("product_id")
            score = event.get("score", 0)
            tenant_id = event.get("tenant_id", "default")

            if score < SCORE_THRESHOLD:
                logger.info("score_below_threshold", product_id=product_id, score=score, threshold=SCORE_THRESHOLD)
                return

            logger.info("hot_product_detected", product_id=product_id, score=score)

            from mq_clients.celery_app import get_celery_app
            import uuid
            task_id = f"pipe_{uuid.uuid4().hex[:12]}"
            app = get_celery_app()
            app.send_task(
                "pipeline_orchestrator.tasks.run_pipeline_task",
                args=[task_id, product_id, tenant_id],
                queue="orchestrator_queue",
            )
            logger.info("pipeline_triggered", task_id=task_id, product_id=product_id)

        except Exception as e:
            logger.error("handle_message_failed", error=str(e), raw=raw[:200])


hot_score_subscriber = HotScoreSubscriber()
```

#### 16.5 routes.py

```python
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["pipeline-orchestrator"])


class CreatePipelineRequest(BaseModel):
    product_id: int
    tenant_id: str = "default"
    config: dict | None = None


@router.post("/pipelines")
async def create_pipeline(request: Request, body: CreatePipelineRequest):
    from mq_clients.celery_app import get_celery_app
    task_id = f"pipe_{uuid.uuid4().hex[:12]}"
    app = get_celery_app()
    app.send_task(
        "pipeline_orchestrator.tasks.run_pipeline_task",
        args=[task_id, body.product_id, body.tenant_id, body.config or {}],
        queue="orchestrator_queue",
    )
    return {"task_id": task_id, "product_id": body.product_id, "status": "queued"}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: int):
    mysql = get_mysql_client()
    await mysql.execute("SELECT * FROM generation_pipelines WHERE id=%s", (pipeline_id,))
    row = await mysql.fetchone()
    if not row:
        return {"error": "Pipeline not found", "pipeline_id": pipeline_id}
    return {"pipeline": row}
```

#### 16.6 main.py

```python
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from contextlib import asynccontextmanager
from fastapi import FastAPI

from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client

from .routes import router
from .subscriber import hot_score_subscriber

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("pipeline_orchestrator_starting")
    try:
        await get_mysql_client().create_pool()
    except Exception as e:
        logger.warning("mysql_init_failed", error=str(e))

    # Redis 不需在 lifespan 初始化 — subscriber 内部自建连接，
    # Celery BaseTask.redis_client 是懒加载
    await hot_score_subscriber.start()

    yield

    await hot_score_subscriber.stop()
    logger.info("pipeline_orchestrator_stopped")


app = FastAPI(title="Pipeline Orchestrator", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "pipeline-orchestrator"}


@app.get("/readyz")
async def readyz():
    try:
        await get_mysql_client().create_pool()
        return {"status": "ready"}
    except Exception as e:
        return {"status": "not_ready", "error": str(e)}
```

#### 16.7 __init__.py

```python
```
(空文件)

#### 16.8 添加 orchestrator_queue 到 Celery

**文件**: `utils/mq_clients/celery_app.py` (L14-20)

```python
TASK_QUEUES = [
    "crawl_queue",
    "analyze_queue",
    "ai_queue",
    "compose_queue",
    "publish_queue",
    "orchestrator_queue",  # 新增
]
```

#### 16.9 注册服务端点

**文件**: `project/backend/mcp_gateway/config.py`

检查并添加 `pipeline-orchestrator` 到 `SERVICE_ENDPOINTS`：
```python
"pipeline-orchestrator": os.getenv("PIPELINE_ORCHESTRATOR_URL", "http://localhost:8008"),
```

---

### Step 17: Pipeline 编排器单元测试

#### 17.1 创建 test_pipeline_orchestrator.py

**文件**: `tests/test_pipeline_orchestrator.py`

6 个测试：

```python
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_mock_resp(data: dict):
    """Helper: create a mock httpx response."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_url_routing_post(route_map: dict):
    """
    Return an AsyncMock for httpx client.post that routes by URL substring.
    route_map: {"copywriting": {"text": "..."}, "images/generate": {"image_objects": [...]}, ...}
    Supports raising exceptions: {"images/generate": RuntimeError("down")}
    """
    async def _post(url, headers=None, json=None):
        for key, val in route_map.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return _make_mock_resp(val)
        raise RuntimeError(f"unexpected url: {url}")
    return AsyncMock(side_effect=_post)


def test_run_pipeline_task_full_dag():
    """测试完整 DAG: analyze → parallel gen → compose → publish"""
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},  # LAST_INSERT_ID()
        {"id": 100, "title": "Test Product", "description": "desc", "main_image_url": "http://img", "tags": ["tag1"]},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 1},
        "copywriting": {"text": "营销文案"},
        "images/generate": {"image_objects": ["prodvideofactory/img/1.jpg"]},
        "videos/generate": {"clip_objects": ["prodvideofactory/clip/1.mp4"]},
        "compose": {"output_object": "prodvideofactory/final/1/1/output.mp4"},
        "publish": {"platform_post_id": "post1", "publish_log_id": 42},
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = _make_url_routing_post(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "httpx.AsyncClient", return_value=mock_client):
        mock_rc.return_value = mock_redis
        result = tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    assert result["pipeline_id"] == 1
    assert "final_video_url" in result
    execute_calls = mock_mysql.execute.call_args_list
    assert any("INSERT INTO generation_pipelines" in str(c) for c in execute_calls)
    assert any("stage" in str(c) and "composing" in str(c) for c in execute_calls)
    assert any("stage" in str(c) and "completed" in str(c) for c in execute_calls)


def test_run_pipeline_task_handles_generation_partial_failure():
    """测试图片生成失败但视频成功 — 应继续合成"""
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test", "description": "d", "main_image_url": "", "tags": []},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 0},
        "copywriting": {"text": "文案"},
        "images/generate": RuntimeError("image API down"),  # fail
        "videos/generate": {"clip_objects": ["c1.mp4"]},
        "compose": {"output_object": "out.mp4"},
        "publish": {"publish_log_id": 42},
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = _make_url_routing_post(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "httpx.AsyncClient", return_value=mock_client):
        mock_rc.return_value = mock_redis
        result = tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    # Should still complete (fail-soft for images)
    assert result["pipeline_id"] == 1
    execute_calls = mock_mysql.execute.call_args_list
    assert any("images_status" in str(c) and "failed" in str(c) for c in execute_calls)


def test_run_pipeline_task_fails_when_no_video_clips():
    """测试视频生成完全失败 — 应抛异常并标记 failed"""
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test", "description": "d", "main_image_url": "", "tags": []},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 0},
        "copywriting": RuntimeError("copy down"),
        "images/generate": RuntimeError("img down"),
        "videos/generate": {"clip_objects": []},  # empty clips
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = _make_url_routing_post(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "httpx.AsyncClient", return_value=mock_client):
        mock_rc.return_value = mock_redis
        with pytest.raises(RuntimeError, match="No video clips generated"):
            tasks.run_pipeline_task.run(
                task_id="t1", product_id=100, tenant_id="default", config=None,
            )

    execute_calls = mock_mysql.execute.call_args_list
    assert any("stage" in str(c) and "failed" in str(c) for c in execute_calls)


@pytest.mark.asyncio
async def test_hot_score_subscriber_triggers_pipeline():
    """测试 Pub/Sub 收到高分商品事件 → send_task"""
    from project.backend.pipeline_orchestrator.subscriber import HotScoreSubscriber

    sub = HotScoreSubscriber()
    mock_celery = MagicMock()
    mock_celery.send_task = MagicMock()

    with patch("mq_clients.celery_app.get_celery_app", return_value=mock_celery):
        await sub._handle_message('{"product_id": 42, "score": 85.5, "tenant_id": "default"}')

    mock_celery.send_task.assert_called_once()
    call_kwargs = mock_celery.send_task.call_args
    assert call_kwargs.kwargs["queue"] == "orchestrator_queue"
    assert call_kwargs.args[1][0].startswith("pipe_")  # task_id
    assert call_kwargs.args[1][1] == 42  # product_id


@pytest.mark.asyncio
async def test_hot_score_subscriber_ignores_low_score():
    """测试低分商品不触发流水线"""
    from project.backend.pipeline_orchestrator.subscriber import HotScoreSubscriber

    sub = HotScoreSubscriber()
    mock_celery = MagicMock()

    with patch("mq_clients.celery_app.get_celery_app", return_value=mock_celery):
        await sub._handle_message('{"product_id": 42, "score": 50.0, "tenant_id": "default"}')

    mock_celery.send_task.assert_not_called()


def test_create_pipeline_route():
    """测试 POST /api/v1/pipelines 路由"""
    from project.backend.pipeline_orchestrator.routes import router, CreatePipelineRequest

    # Verify route exists and has correct path
    routes = [r.path for r in router.routes]
    assert "/api/v1/pipelines" in routes
    assert "/api/v1/pipelines/{pipeline_id}" in routes
```

---

## 假设与约定

1. **服务间认证**: 编排器使用 `create_service_jwt("pipeline-orchestrator", JWT_SECRET)` 生成 JWT，各服务通过 `verify_internal_jwt` 验证
2. **MySQL 客户端**: `get_mysql_client()` 返回单例，`execute()` 是 async，`fetchone()` 是 async
3. **Redis 客户端**: `BaseTask.redis_client` 属性返回同步 `redis.Redis` 实例（Celery 任务中用）
4. **httpx 超时**: 生成任务可能耗时长（视频生成 2-5 分钟），设置 `timeout=300.0`
5. **并行生成**: `asyncio.gather(return_exceptions=True)` — 单个生成失败不阻塞其他
6. **fail-soft 策略**: 文案和图片失败可继续（视频合成不依赖它们），视频片段失败则整条流水线失败
7. **product_id 类型**: `generation_pipelines.product_id` 是 `BIGINT`，product 表 id 也是 `BIGINT`
8. **pipeline_id 传递**: 编排器创建 pipeline 后，将 `pipeline_id` 传给 video-composer 和 publish-dispatcher 的 HTTP 端点
9. **orchestrator_queue**: 新增 Celery 队列，需要 RabbitMQ 中已创建（或使用自动声明）
10. **import 路径一致性**: 所有新代码使用无 `utils.` 前缀的导入（因为 `sys.path.insert` 已包含 `utils/` 目录）

---

## 验证步骤

### 验证 1: Phase 3 测试全部通过

```bash
cd c:\Users\29048\PycharmProjects\PythonProject1
python -m pytest tests/test_video_composer_phase3.py tests/test_publish_dispatcher_phase3.py tests/test_asset_manager_phase3.py -v
```
预期: 12 passed

### 验证 2: Phase 4 新测试通过

```bash
python -m pytest tests/test_pipeline_orchestrator.py -v
```
预期: 6 passed

### 验证 3: 无硬编码 Redis URL

```bash
# Grep 确认没有硬编码的 dev_redis_2024
# 在 crawl_scheduler/tasks.py 和 mcp_gateway/tool_handlers.py 中
```

### 验证 4: 所有测试回归

```bash
python -m pytest tests/ -v --tb=short
```
预期: 所有之前的测试仍通过 + 新测试通过

### 验证 5: import 一致性检查

```bash
# 确认 video_composer/tasks.py 和 publish_dispatcher/tasks.py 中没有 utils. 前缀导入
```

---

## 文件清单

### 修改的文件（8 个）

| 文件 | 修改内容 |
|------|---------|
| `tests/test_video_composer_phase3.py` | 14.1: 3 个测试添加 `returncode=0` |
| `project/backend/publish_dispatcher/worker_publishers.py` | 14.2: 为每个平台注册 GenericHTTPPublisher |
| `project/backend/video_composer/tasks.py` | 14.3: 移除 `utils.` 前缀导入 |
| `project/backend/publish_dispatcher/tasks.py` | 14.3: 移除 `utils.` 前缀导入 |
| `project/backend/crawl_scheduler/tasks.py` | 15.1: 移除硬编码 Redis URL + 添加 self 参数 |
| `project/backend/mcp_gateway/tool_handlers.py` | 15.2: 移除硬编码 Redis URL + 补充 ai-generation 查询 |
| `utils/mq_clients/celery_app.py` | 16.8: 添加 `orchestrator_queue` |
| `project/backend/mcp_gateway/config.py` | 16.9: 添加 pipeline-orchestrator 端点 |

### 新建的文件（7 个）

| 文件 | 内容 |
|------|------|
| `project/backend/pipeline_orchestrator/__init__.py` | 空包初始化 |
| `project/backend/pipeline_orchestrator/config.py` | 服务配置 |
| `project/backend/pipeline_orchestrator/main.py` | FastAPI 入口 + lifespan subscriber |
| `project/backend/pipeline_orchestrator/routes.py` | HTTP 路由 |
| `project/backend/pipeline_orchestrator/tasks.py` | DAG 编排 Celery 任务 |
| `project/backend/pipeline_orchestrator/subscriber.py` | Redis Pub/Sub 监听器 |
| `tests/test_pipeline_orchestrator.py` | 6 个单元测试 |
