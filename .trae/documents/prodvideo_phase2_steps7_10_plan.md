# ProdVideo AI Factory — Phase 2 深度开发计划 (Steps 7-10)

> 承接前序工作：Steps 0-6 已完成（auth 导入修复、`registry_manager._build_real_adapter`、7 个云适配器重写含同步包装器/Vault 注入/MinIO 上传/长任务轮询、`_minio_helper.py` 创建）。
>
> 本计划覆盖剩余 Steps 7-10，目标：让 `ai_generation` 微服务的 HTTP 层与 Celery 任务层都真正调用云适配器，并修复 `CostCalculator` 的 HTTP 自递归缺陷。

---

## 一、当前状态分析（基于 Phase 1 探查实证）

| # | 缺陷 | 文件:行 | 影响 |
|---|---|---|---|
| D1 | `routes.py` 5 个 `/internal/*` + `/models` 端点每次请求重建 `RegistryManager()` + `ModelRouterService` | `routes.py:174-176, 211, 246, 285, 322` | 每请求重复注册 7 适配器、丢失败统计、性能差 |
| D2 | `main.py:/metrics` 同样每次重建 | `main.py:161-163` | 同上 |
| D3 | `routes.py` 在 `async def` 端点内调用同步 `adapter.chat/generate/synthesize` | `routes.py:220, 255, 294, 331` | 同步包装器内 `asyncio.run()` 在已运行的事件循环中调用 → `RuntimeError: asyncio.run() cannot be called from a running event loop`，端点必崩 |
| D4 | `tasks.py` 4 个 Celery 任务返回 Mock URL，不调用任何适配器 | `tasks.py:98, 156, 197` | 任务层 100% 假数据，不满足"全真实实现"要求 |
| D5 | `tasks.py` 无 `tenant_id` 参数 | `tasks.py:14, 74, 130, 175` | 适配器 MinIO 前缀无法按租户隔离 |
| D6 | `worker_router.py` 不存在 | （Glob 确认缺失） | Celery worker 不运行 FastAPI lifespan，无法获取 `_router_service` 单例 |
| D7 | `CostCalculator.log_usage` 通过 `httpx.post` 发往 `/api/v1/internal/usage/log` | `utils/model_adapters/cost.py:104-106` | ① Celery worker 无 FastAPI 服务，POST 失败；② 递归 HTTP 依赖；③ 异常被静默吞掉，用量永不记录 |
| D8 | `db_clients/mysql.py` 仅异步（`aiomysql`），无同步连接函数 | `utils/db_clients/mysql.py:7, 47` | `log_usage` 当前是同步方法，无法直接 `await mysql.execute(...)` |
| D9 | 返回形状不一致：适配器返回 `{"clip_objects": [...]}`，tasks mock 返回 `{"clip_urls": [...]}` | `video_veo3.py:122`, `tasks.py:166` | 任务层对接适配器后需统一键名 |

### 关键已确认事实

- `main.py:40` 已有模块级 `_router_service: ModelRouterService | None = None`，在 lifespan L78-80 赋值，但未暴露为 FastAPI 依赖。
- `adapters/__init__.py` 正确导出全部 7 个适配器类，`registry_manager.py:13-21` 的 `from .adapters import (...)` 可解析。
- 所有适配器的 `*_async` 方法已就绪（`chat_async`/`generate_async`/`synthesize_async`），且 `log_usage` 调用发生在 `*_async` 方法内部（如 `llm_openai.py:92`），即在事件循环已运行的协程中调用。
- `routes.py:343-372` 的 `/internal/usage/log` 端点已做直接 MySQL INSERT（L348, L370），可作为 `log_usage` 重写的参照模板。
- `main.py:90-112` 的 `_ensure_usage_log_table` 已定义 `model_usage_log` 表结构（`adapter_id, adapter_type, model, pipeline_id, tenant_id, input_tokens, output_tokens, image_count, duration_seconds, estimated_cost, status, created_at`）。
- `BaseTask`（来自 `mq_clients.celery_app`）提供 `self.redis_client`（同步），tasks.py 已用于状态更新。

---

## 二、设计决策（无需用户再确认，基于已批准方案）

1. **返回形状统一为适配器形状**：tasks.py 改为返回 `{"image_objects": [...]}` / `{"clip_objects": [...]}` / `{"audio_object": ...}` / `{"text": ...}`，与适配器一致。废弃 `*_urls` 键名。（决策依据：适配器已实现且 MinIO object name 比 presigned URL 更持久——URL 会过期。）

2. **`CostCalculator.log_usage` 改为异步直接 MySQL 写**：
   - 新增 `async def log_usage_async(self, record: dict) -> None`，内部 `await mysql_client.execute(INSERT SQL, params)`。
   - 保留原 `log_usage` 同步方法但标记 `DeprecationWarning`，内部改为"尽力而为"——用 `asyncio.run(self.log_usage_async(record))` 包装（仅在无事件循环时可用，作为非热路径兜底）。
   - 适配器 `*_async` 方法内改为 `await self.cost_calculator.log_usage_async(record)`。
   - 不引入 `pymysql` 同步依赖，复用既有 `aiomysql` 单例。

3. **`worker_router.py` 作为模块级单例**：Celery worker 进程导入即初始化一次 `RegistryManager().register_default_adapters()` + `ModelRouterService(...)`。不依赖 FastAPI lifespan。

4. **`tasks.py` 任务改为同步 `def`（保持 Celery 兼容）+ 内部 `asyncio.run(adapter.*_async(...))`**：Celery 任务默认同步执行，`asyncio.run` 在 worker 进程中创建独立事件循环，安全。与 `routes.py` 端点（已在事件循环内，用 `await`）区分。

5. **内容安全补全**：`tasks.py` 任务 3（视频）和任务 4（TTS）当前无内容安全检查。视频任务对生成结果 URL 做图像安全检查（`check_image`）；TTS 任务对输入文本做 `check_text`。

---

## 三、实施步骤

### Step 7: `routes.py` + `main.py` 复用 lifespan `_router_service`

**目标**：消除 D1/D2/D3。

**7.1 `main.py` 新增 `get_router_service` 依赖函数**

在 `main.py` 顶部 `_router_service` 声明（L40）之后，新增：

```python
def get_router_service() -> "ModelRouterService":
    if _router_service is None:
        raise ServiceException(code=503, message="Router service not initialized")
    return _router_service
```

（`ServiceException` 从 `common_sdk.exceptions` 导入。）

**7.2 `main.py:/metrics` 改用单例**

将 L161-163 的 `RegistryManager()...ModelRouterService(...)` 三行替换为直接读取模块级 `_router_service`（已是全局变量，`/metrics` 在同文件内可直接访问）。若 `_router_service is None` 返回 503。

**7.3 `routes.py` 5 个端点改用 `Depends(get_router_service)`**

对 `/models`（L166）、`/internal/llm/chat`（L198）、`/internal/image/generate`（L231）、`/internal/video/generate`（L268）、`/internal/tts/synthesize`（L309）共 5 个端点：

- 签名新增参数 `router_svc: ModelRouterService = Depends(get_router_service)`。
- 删除函数体内 `reg_manager = RegistryManager(); reg_manager.register_default_adapters(); router_svc = ModelRouterService(reg_manager.registry)` 三行。
- 顶部新增 `from .main import get_router_service` 与 `from .router import ModelRouterService`（提到模块级，不再 lazy import）。

**7.4 `routes.py` 同步调用改异步**

| 行 | 原 | 改为 |
|---|---|---|
| L220 | `result = adapter.chat(messages=..., max_tokens=..., temperature=...)` | `result = await adapter.chat_async(messages=..., max_tokens=..., temperature=...)` |
| L255 | `result = adapter.generate(prompt=..., n=..., size=..., tenant_id=..., pipeline_id=...)` | `result = await adapter.generate_async(prompt=..., n=..., size=..., tenant_id=..., pipeline_id=...)` |
| L294 | `result = adapter.generate(prompt=..., duration=..., aspect_ratio=..., tenant_id=..., pipeline_id=..., task_id=...)` | `result = await adapter.generate_async(prompt=..., duration=..., aspect_ratio=..., tenant_id=..., pipeline_id=..., task_id=...)` |
| L331 | `result = adapter.synthesize(text=..., voice=..., tenant_id=..., pipeline_id=...)` | `result = await adapter.synthesize_async(text=..., voice=..., tenant_id=..., pipeline_id=...)` |

（端点已是 `async def`，直接 `await`。）

**7.5 验证**

- `python -c "import ast; ast.parse(open('project/backend/ai_generation/routes.py').read())"` 语法检查。
- 确认 `routes.py` 中无 `RegistryManager()` 字样（Grep）。
- 确认 `routes.py` 中无 `adapter.chat(` / `adapter.generate(` / `adapter.synthesize(` 同步调用（Grep `adapter\.\w+\(` 应只剩 `*_async`）。

---

### Step 8: `tasks.py` + 新建 `worker_router.py` — Celery 任务真实化

**目标**：消除 D4/D5/D6。

**8.1 新建 `project/backend/ai_generation/worker_router.py`**

```python
"""Celery worker 专用的路由服务单例。

Celery worker 进程不运行 FastAPI lifespan，因此无法复用 main._router_service。
本模块在导入时初始化一次 RegistryManager + ModelRouterService，供所有任务共享。
"""
from __future__ import annotations

import logging

from .registry_manager import RegistryManager
from .router import ModelRouterService

logger = logging.getLogger(__name__)

_reg_manager = RegistryManager()
_reg_manager.register_default_adapters()
worker_router: ModelRouterService = ModelRouterService(_reg_manager.registry)

__all__ = ["worker_router"]
```

**8.2 重写 `tasks.py` 4 个任务**

通用模式（以 `generate_images_task` 为例）：

```python
from .worker_router import worker_router
from db_clients.minio import get_minio_client

@celery_app.task(bind=True, base=BaseTask, name="ai_generation.tasks.generate_images_task",
                 queue="ai_queue", max_retries=3)
def generate_images_task(self, payload: dict, tenant_id: str = "default", pipeline_id: str | None = None):
    task_id = self.request.id
    try:
        self.redis_client.hset(f"task:{task_id}", mapping={"status": "processing", "progress": "0"})
        # 内容安全（已有，保留）
        ...
        preferred = payload.get("preferred_model")
        adapter = router_svc.route_image(preferred_model=preferred, product_tier=payload.get("product_tier", "standard"))
        if adapter is None:
            raise RuntimeError("No healthy image adapter available")
        # Celery 同步任务 → asyncio.run 调用 async 适配器
        result = asyncio.run(adapter.generate_async(
            prompt=payload["prompt"], n=payload.get("n", 1),
            size=payload.get("size", "1024x1024"),
            tenant_id=tenant_id, pipeline_id=pipeline_id,
        ))
        self.redis_client.hset(f"task:{task_id}", mapping={"status": "success", "progress": "100"})
        return result  # {"image_objects": [...]}
    except Exception as e:
        self.redis_client.hset(f"task:{task_id}", mapping={"status": "failed", "error": str(e)})
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
```

4 个任务逐一改造：

| 任务 | 路由方法 | 适配器调用 | 返回 |
|---|---|---|---|
| `generate_copywriting_task` | `router_svc.route_llm(...)` | `asyncio.run(adapter.chat_async(messages, max_tokens, temperature))` | `{"text": ...}` |
| `generate_images_task` | `router_svc.route_image(...)` | `asyncio.run(adapter.generate_async(prompt, n, size, tenant_id, pipeline_id))` | `{"image_objects": [...]}` |
| `generate_video_clips_task` | `router_svc.route_video(...)` | `asyncio.run(adapter.generate_async(prompt, duration, aspect_ratio, tenant_id, pipeline_id, task_id))` | `{"clip_objects": [...]}` |
| `tts_synthesize_task` | `router_svc.route_tts(...)` | `asyncio.run(adapter.synthesize_async(text, voice, tenant_id, pipeline_id))` | `{"audio_object": ...}` |

**8.3 路由方法名确认**

需在 Step 8.2 实施前 Grep 确认 `ModelRouterService` 的路由方法签名：`route_llm` / `route_image` / `route_video` / `route_tts` 的参数名（`preferred_model`、`product_tier`）。若签名不符，调整调用。

**8.4 任务签名统一增加 `tenant_id` 与 `pipeline_id`**

4 个任务 `def fn(self, payload, tenant_id="default", pipeline_id=None)`。

**8.5 `routes.py` 入队端点透传 `tenant_id`**

`/copywriting`（L47）、`/images/generate`（L77）、`/videos/generate`（L107）、`/tts/synthesize`（L138）4 个入队端点，调用 `task.delay(payload, tenant_id, pipeline_id)` 时从 `request.state.tenant_id`（由 `verify_jwt` 注入）取 `tenant_id`，从 payload 取 `pipeline_id`。

**8.6 内容安全补全**

- `generate_video_clips_task`：生成后对 `clip_objects` 做 `content_safety_client.check_image`（用 presigned URL 或下载后检查——按既有 `check_image` 签名决定）。
- `tts_synthesize_task`：生成前对 `text` 做 `content_safety_client.check_text`。

**8.7 验证**

- `python -c "import ast; ast.parse(open('project/backend/ai_generation/tasks.py').read())"` 语法检查。
- `python -c "import ast; ast.parse(open('project/backend/ai_generation/worker_router.py').read())"` 语法检查。
- Grep `storage.prodvideo.local` 在 `tasks.py` 应 0 命中。
- Grep `asyncio.run(adapter.` 在 `tasks.py` 应 4 命中。

---

### Step 9: `CostCalculator.log_usage` 改异步直接 MySQL 写

**目标**：消除 D7/D8。

**9.1 `utils/model_adapters/cost.py` 改造**

```python
class CostCalculator:
    _INTERNAL_USAGE_ENDPOINT = "/api/v1/internal/usage/log"  # 保留常量，标记弃用

    def calculate_cost(self, adapter_type: str, model: str, **kwargs) -> float:
        ...  # 不变

    async def log_usage_async(self, record: dict) -> None:
        """直接写 MySQL model_usage_log 表。"""
        from db_clients.mysql import get_mysql_client
        try:
            mysql = get_mysql_client()
            sql = (
                "INSERT INTO model_usage_log "
                "(adapter_id, adapter_type, model, pipeline_id, tenant_id, "
                " input_tokens, output_tokens, image_count, duration_seconds, "
                " estimated_cost, status) "
                "VALUES (%(adapter_id)s, %(adapter_type)s, %(model)s, %(pipeline_id)s, "
                " %(tenant_id)s, %(input_tokens)s, %(output_tokens)s, %(image_count)s, "
                " %(duration_seconds)s, %(estimated_cost)s, %(status)s)"
            )
            await mysql.execute(sql, record)
        except Exception as e:
            logger.warning(f"log_usage_async failed: {e}")  # fail-soft，不阻塞主流程

    def log_usage(self, record: dict, endpoint: str | None = None) -> None:
        """已弃用：HTTP POST 路径。保留仅为向后兼容，内部转异步。"""
        import warnings
        warnings.warn("log_usage is deprecated, use log_usage_async", DeprecationWarning, stacklevel=2)
        try:
            asyncio.run(self.log_usage_async(record))
        except RuntimeError:
            logger.warning("log_usage called inside running event loop; skipped. Use log_usage_async.")
        except Exception as e:
            logger.warning(f"log_usage fallback failed: {e}")
```

（删除原 L85-107 的 `httpx.post` 逻辑；`_PRICE_TABLE`、`UsageLogRequest`、`calculate_cost` 不变。）

**9.2 适配器调用点改为 `await log_usage_async`**

7 个适配器的 `*_async` 方法内，将 `self.cost_calculator.log_usage(record)` 改为 `await self.cost_calculator.log_usage_async(record)`。涉及：

- `llm_openai.py`（chat_async 内）
- `llm_claude.py`（chat_async 内）
- `image_dalle.py`（generate_async 内）
- `image_comfyui.py`（generate_async 内）
- `video_veo3.py`（generate_async 内）
- `video_sora.py`（generate_async 内）
- `tts_azure.py`（synthesize_async 内）

**9.3 验证**

- Grep `httpx.post` 在 `utils/model_adapters/cost.py` 应 0 命中。
- Grep `log_usage(` 在 `adapters/` 目录：同步 `log_usage(` 应 0 命中，`await ... log_usage_async(` 应 7 命中。
- `python -c "import ast; ast.parse(open('utils/model_adapters/cost.py').read())"` 语法检查。

---

### Step 10: 编写 `tests/test_ai_generation_phase2.py` 单元测试

**目标**：验证 Steps 7-9 的关键契约，不依赖真实云 API（用 mock）。

**测试文件**：`c:\Users\29048\PycharmProjects\PythonProject1\tests\test_ai_generation_phase2.py`

**10 个测试用例**：

| # | 测试名 | 验证点 |
|---|---|---|
| 1 | `test_get_router_service_returns_singleton` | `get_router_service()` 返回 lifespan 初始化的同一对象 |
| 2 | `test_get_router_service_raises_when_not_initialized` | `_router_service is None` 时抛 503 |
| 3 | `test_routes_internal_llm_chat_uses_async` | mock 适配器，POST `/internal/llm/chat`，断言 `chat_async` 被调用（而非 `chat`） |
| 4 | `test_routes_internal_image_generate_uses_async` | 同上，断言 `generate_async` |
| 5 | `test_routes_internal_video_generate_uses_async` | 同上 |
| 6 | `test_routes_internal_tts_synthesize_uses_async` | 同上，断言 `synthesize_async` |
| 7 | `test_worker_router_singleton_exists` | `from ai_generation.worker_router import worker_router` 可导入且非 None |
| 8 | `test_tasks_generate_images_returns_image_objects` | mock `route_image` 返回 mock 适配器，调用 `generate_images_task`，断言返回含 `image_objects` 键、无 `storage.prodvideo.local` |
| 9 | `test_cost_calculator_log_usage_async_writes_mysql` | mock `get_mysql_client().execute`，调用 `await log_usage_async(record)`，断言 `execute` 被调用且 SQL 含 `INSERT INTO model_usage_log` |
| 10 | `test_cost_calculator_log_usage_deprecated_warns` | 调用同步 `log_usage`，断言抛 `DeprecationWarning` |

**测试约定**：
- 用 `pytest` + `pytest-asyncio`（项目既有约定）。
- mock 适配器：`unittest.mock.AsyncMock`，`route_*` 返回 mock 适配器实例。
- 不真实连接 MySQL/Redis/MinIO，全部 mock。
- FastAPI 端点测试用 `httpx.AsyncClient` + `app` fixture。

**10.1 验证**

- `python -m pytest tests/test_ai_generation_phase2.py -v` 10/10 通过。

---

## 四、假设与前提

1. **`ModelRouterService` 路由方法签名**：假设为 `route_llm(preferred_model=None, product_tier="standard")` / `route_image(...)` / `route_video(...)` / `route_tts(...)`。Step 8.3 会 Grep 确认，不符则调整。
2. **`get_mysql_client()` 返回单例且 `execute(sql, params)` 已实现**：基于 `db_clients/mysql.py:69` 已有 `execute` 方法。
3. **Celery `BaseTask` 提供 `self.redis_client`**：基于 `tasks.py` 现有代码已用。
4. **`request.state.tenant_id` 在 `verify_jwt` 后可用**：基于 `common_sdk/auth.py:66` 已设。
5. **`content_safety_client.check_text/check_image` 签名不变**：基于 `tasks.py` 现有调用。
6. **适配器 `*_async` 方法签名与 Step 7.4 表格一致**：基于摘要中记录的适配器重写。若实际签名有出入（如 `pipeline_id` 是否必填），实施时按实际调整。

---

## 五、文件清单

| 文件 | 操作 | Step |
|---|---|---|
| `project/backend/ai_generation/main.py` | 修改：新增 `get_router_service`，`/metrics` 用单例 | 7 |
| `project/backend/ai_generation/routes.py` | 修改：5 端点用 `Depends`，同步调用改 `await *_async`，入队端点透传 `tenant_id` | 7, 8 |
| `project/backend/ai_generation/worker_router.py` | 新建 | 8 |
| `project/backend/ai_generation/tasks.py` | 重写：4 任务真实化，加 `tenant_id`/`pipeline_id`，返回 `*_objects` | 8 |
| `utils/model_adapters/cost.py` | 修改：`log_usage_async` 直接 MySQL 写，`log_usage` 标弃用 | 9 |
| `project/backend/ai_generation/adapters/llm_openai.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/llm_claude.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/image_dalle.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/image_comfyui.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/video_veo3.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/video_sora.py` | 修改：`await log_usage_async` | 9 |
| `project/backend/ai_generation/adapters/tts_azure.py` | 修改：`await log_usage_async` | 9 |
| `tests/test_ai_generation_phase2.py` | 新建：10 单元测试 | 10 |

---

## 六、验证总览

每个 Step 完成后执行该 Step 的"验证"小节。全部完成后：

1. `python -m pytest tests/test_auth_unified.py tests/test_ai_generation_phase2.py -v` 全绿。
2. Grep 全局确认：
   - `routes.py` 无 `RegistryManager()`、无同步 `adapter.chat/generate/synthesize(`
   - `tasks.py` 无 `storage.prodvideo.local`、有 4 处 `asyncio.run(adapter.`
   - `cost.py` 无 `httpx.post`、有 `log_usage_async`
   - `adapters/` 有 7 处 `await self.cost_calculator.log_usage_async(`
3. 各文件 `ast.parse` 语法检查通过。
