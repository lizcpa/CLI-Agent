# ProdVideo AI Factory — Phase 2 实施方案：ai-generation 任务层接真实云适配器

## Summary

承接已批准的 `prodvideo_deep_dev_plan.md` Phase 1（基础设施与 Bug 修复），本方案聚焦两件事：

1. **Phase 1 收尾修一个回归**：`tests/test_auth_unified.py` 8/9 通过，第 9 个 `test_service_auth_reexports_verify_internal_jwt` 失败。根因是 4 个服务的 `auth.py` 使用了两套不同的 import 路径（`from utils.common_sdk.auth` vs `from common_sdk.auth`），Python 将同一文件加载为两个模块对象，导致 `verify_internal_jwt` 不是同一个函数对象。
2. **Phase 2 主体**：消除 ai-generation 任务层 100% Mock，打通"路由 → 真实云适配器 → MinIO 落盘 → Redis 结果"全链路。修复 `registry_manager._build_adapter_stub` 造裸对象、4 个 Celery 任务返假 URL、`/internal/*` 端点每请求重建 RegistryManager、云适配器缺同步方法、缺 MinIO 上传、缺长任务轮询、ComfyUI workflow 空壳、TTS 丢弃音频字节、CostCalculator 日志发错地址等 11 处缺陷。

用户已授权"后续内容你直接跟进就行"，本方案为可执行级，无遗留决策。

---

## Current State Analysis

### Phase 1 状态（已验证）

- `utils/common_sdk/{auth,nacos_client,vault_client,content_safety,registry_config}.py` 全部就绪 ✓
- `tests/test_auth_unified.py` 8/9 通过；第 9 个失败（import 路径不一致导致 `is` 不等）❌
- 4 个服务 `auth.py` 已下沉 re-export `verify_internal_jwt`，但 import 路径分两派：
  - `product_analyzer/auth.py:3`、`publish_dispatcher/auth.py:3`：`from utils.common_sdk.auth import verify_internal_jwt`
  - `crawl_scheduler/auth.py:3`、`ai_generation/auth.py:3`：`from common_sdk.auth import verify_internal_jwt`
  - 测试 `tests/test_auth_unified.py:7` 注入 `utils/` 到 `sys.path`，使 `common_sdk` 可作顶层包导入；但项目根也在 path 上，使 `utils.common_sdk` 也可导入 → 同一 `.py` 被加载为两个模块对象，函数 `id` 不同。

### Phase 2 现状（已审计 11 处缺陷）

| # | 缺陷 | 文件:行 | 证据 |
|---|---|---|---|
| 1 | `_build_adapter_stub` 用 `BaseModelAdapter.__new__(BaseModelAdapter)` 造裸对象，不实例化真实子类 | `project/backend/ai_generation/registry_manager.py:30-43` | `adapter = BaseModelAdapter.__new__(BaseModelAdapter)` |
| 2 | 4 个 Celery 任务 100% Mock，返 `storage.prodvideo.local/...` 假 URL，从不调 router | `project/backend/ai_generation/tasks.py:39,98,156,197` | 4 处硬编码 URL |
| 3 | `/internal/*` 端点每请求重建 `RegistryManager()` + `register_default_adapters()`，不复用 lifespan `_router_service` | `routes.py:174-176,211-213,246-248,285-287,322-324` | 5 处 `reg_manager = RegistryManager()` |
| 4 | 5 个云适配器（DALLE/ComfyUI/Veo3/Sora/Azure）只有 `*_async`，无同步 `generate/synthesize`；继承自 `BaseImageAdapter/BaseVideoAdapter/BaseTTSAdapter` 的同步方法 posts 到 `_internal_endpoint`（云 URL + `/api/v1/internal/...`，错误地址） | `adapters/image_dalle.py`、`image_comfyui.py`、`video_veo3.py`、`video_sora.py`、`tts_azure.py` | 均无 `def generate` / `def synthesize`；基类 `utils/model_adapters/{image,video,tts}.py` 的 `generate/synthesize` posts to `self._internal_endpoint` |
| 5 | OpenAI/Claude LLM 适配器有重复同步 `chat` 实现（与 `chat_async` 逻辑重复） | `adapters/llm_openai.py:88-116`、`llm_claude.py:100-142` | 同步版重新写了一遍 httpx.post |
| 6 | 适配器用 `os.getenv("OPENAI_API_KEY")` 直读 env，未走 `vault_client.get_model_credential` | 所有 7 个适配器 | `import os; api_key = os.getenv(...)` |
| 7 | 适配器返回云 URL/文本，从不上传 MinIO | 所有 7 个适配器 | DALLE 返 OpenAI URL；TTS 返 `{"audio_url": ""}` 空串；Veo3/Sora 返 `{"task_id": ...}` 不下载视频 |
| 8 | Veo3/Sora 长任务不轮询 operations | `adapters/video_veo3.py:88`、`video_sora.py:89` | `get_result` 返 `{"clip_urls": []}` 空列表 |
| 9 | ComfyUI `_build_workflow` 返 `{"prompt": {}}` 空工作流 | `adapters/image_comfyui.py:103-106` | `"prompt": {}` |
| 10 | Azure TTS 丢弃 `resp.content` 音频字节 | `adapters/tts_azure.py:65-67` | `resp = await client.post(...)` 后无 `resp.content` 读取 |
| 11 | `CostCalculator.log_usage` posts 到 `self.endpoint + /api/v1/internal/usage/log`，但 `self.endpoint` 是云 URL（如 `https://api.openai.com`）→ 用量日志发到云厂商，丢失 | `utils/model_adapters/cost.py:90` + 各适配器 `self._cost_calc.log_usage(record, self.endpoint)` | endpoint 传参是云 URL |

### 关键架构事实（已读代码确认）

- **两套同名适配器**：`utils/model_adapters/{llm,image,video,tts}.py` 内的 `OpenAILLMAdapter` 等是**内部 REST 桩**（posts to `self._internal_endpoint`）；`project/backend/ai_generation/adapters/*.py` 内的同名类是**真实云实现**（calls `self.endpoint` 直连云 API）。`registry_manager.py:7-8` 当前 import 桩基类，Phase 2 必须改 import 真实子类。
- **`ModelRouterService` 已就绪**（`router.py`）：`route_llm/image/video/tts` + `router.track_failure/track_success` 已存在；`ModelRouter.route` 已支持 `preferred_model` + `product_tier` 优先级 + 3 次失败降级 5 分钟。
- **MinIO 客户端 API**（`utils/db_clients/minio.py`）：`upload_file(bucket, object_name, file_path_or_data: str|bytes, content_type)` / `upload_stream(bucket, object_name, data_stream, length, content_type)` / `get_presigned_url(bucket, object_name, expires_seconds=3600)` / `download_file(bucket, object_name, file_path)`。
- **Vault 凭证 API**（`utils/common_sdk/vault_client.py`）：`vault_client.get_model_credential(adapter_id) -> dict | None`，`VAULT_ENABLED=false` 时自动 fallback 到 env（`_ENV_MAP` 已映射 7 个 adapter_id）。
- **`main.py` lifespan** 已建 `_router_service` 单例（`main.py:80`），但 `routes.py` 不用；Celery worker 不跑 lifespan，需独立初始化。
- **架构 V4.1 §3.3**：路由策略 = 指定模型直接用 / `tier=hot` 选最高优先级 / `tier=normal` 选 cost_tag=free|low / 连续 3 次失败降级 5 分钟 / 每次调用后写 `model_usage_log`。
- **架构 V4.1 §3.1**：内部接口规范 = LLM 同步返 text；Image/Video/TTS 异步返 task_id，再轮询 `/internal/tasks/{task_id}/result`。

---

## Proposed Changes

### Step 0: Phase 1 收尾 — 统一 auth import 路径

**目标**：让 `tests/test_auth_unified.py` 9/9 通过。

**改动**：
- `project/backend/product_analyzer/auth.py:3`：`from utils.common_sdk.auth import verify_internal_jwt` → `from common_sdk.auth import verify_internal_jwt`
- `project/backend/publish_dispatcher/auth.py:3`：同上

**为什么**：`crawl_scheduler`、`ai_generation`、`tests/` 都用 `from common_sdk.auth`（因为 `sys.path` 注入 `utils/`）。统一后 Python 只加载一份 `common_sdk.auth` 模块，`is` 检查通过。`product_analyzer/main.py` 和 `publish_dispatcher/main.py` 已通过自身 `sys.path.insert` 注入 `utils/`（验证：当前 `from utils.common_sdk.auth` 能 import 成功说明项目根在 path 上；改成 `from common_sdk.auth` 需 `utils/` 在 path 上——这两个服务的 `main.py` 已有此注入，与 `ai_generation/main.py:5` 同模式）。

**验证**：先读 `product_analyzer/main.py` 和 `publish_dispatcher/main.py` 确认 `sys.path` 注入 `utils/`，再改。改后跑 `pytest tests/test_auth_unified.py` 9/9。

---

### Step 1: `registry_manager._build_adapter_stub` → `_build_real_adapter`

**文件**：`project/backend/ai_generation/registry_manager.py`

**改动**：
1. 删 `from model_adapters.base import BaseModelAdapter`、`from model_adapters.registry import AdapterRegistry`。
2. 改 `from common_sdk.registry_config import load_model_registry`（保留）。
3. 新增 `from .adapters import (OpenAILLMAdapter, ClaudeLLMAdapter, ComfyUIImageAdapter, DALLEImageAdapter, Veo3VideoAdapter, SoraVideoAdapter, AzureTTSAdapter)`。
4. 保留 `from model_adapters.registry import AdapterRegistry`（`AdapterRegistry` 是注册表容器，不是桩适配器，可继续用）。
5. 删 `_build_adapter_stub`，新增 `_build_real_adapter(cfg: dict) -> BaseModelAdapter`：

```python
_ADAPTER_MAP: dict[tuple[str, str], type[BaseModelAdapter]] = {
    ("openai_rest", "llm"): OpenAILLMAdapter,
    ("anthropic_rest", "llm"): ClaudeLLMAdapter,
    ("comfyui_api", "image"): ComfyUIImageAdapter,
    ("openai_rest", "image"): DALLEImageAdapter,
    ("google_vertex", "video"): Veo3VideoAdapter,
    ("openai_rest", "video"): SoraVideoAdapter,
    ("azure_cognitive", "tts"): AzureTTSAdapter,
}

def _build_real_adapter(cfg: dict) -> BaseModelAdapter:
    cls = _ADAPTER_MAP.get((cfg.get("protocol", ""), cfg["type"]))
    if cls is None:
        raise ValueError(f"Unsupported adapter protocol={cfg.get('protocol')} type={cfg['type']}")
    return cls(
        adapter_id=cfg["id"],
        model=cfg["id"],
        endpoint=cfg["endpoint"],
        protocol=cfg.get("protocol", ""),
        priority=cfg.get("priority", 10),
        max_concurrency=cfg.get("max_concurrency", 5),
        capabilities=cfg.get("capabilities", {}),
    )
```

6. `register_default_adapters` 改调 `_build_real_adapter`。

**为什么**：消除"造裸对象"缺陷 #1。凭证不在构造时注入（适配器内部按需调 `vault_client`，见 Step 6），避免构造时失败拖垮整个注册表。

---

### Step 2: 统一云适配器同步/异步契约

**文件**：`project/backend/ai_generation/adapters/{image_dalle,image_comfyui,video_veo3,video_sora,tts_azure}.py` + `llm_openai.py` + `llm_claude.py`

**改动**：每个缺同步方法的适配器新增同步包装：

```python
# image_dalle.py / image_comfyui.py 新增
def generate(self, prompts, size="1024x1024", n=1, negative_prompt=None, seed=None):
    import asyncio
    return asyncio.run(self.generate_async(prompts, size, n, negative_prompt, seed))

# video_veo3.py / video_sora.py 新增
def generate(self, type, prompts, reference_image_url=None, duration=5, resolution="1080x1920", count=1, motion_strength=0.8):
    import asyncio
    return asyncio.run(self.generate_async(type, prompts, reference_image_url, duration, resolution, count, motion_strength))

# tts_azure.py 新增
def synthesize(self, text, voice="default", language="zh", speed=1.0):
    import asyncio
    return asyncio.run(self.synthesize_async(text, voice, language, speed))
```

**llm_openai.py / llm_claude.py**：删重复的同步 `chat` 实现，改为：

```python
def chat(self, messages, max_tokens=2048, temperature=0.7):
    import asyncio
    return asyncio.run(self.chat_async(messages, max_tokens, temperature))
```

**为什么**：消除缺陷 #4、#5。单一真实实现 = `*_async`，同步方法仅包装。Celery 任务用 `asyncio.run`，FastAPI 端点用 `await`。注意：`BaseImageAdapter.generate`（桩基类）会被新方法覆盖，不再 posts 到 `_internal_endpoint`。

**注意**：`asyncio.run` 在已有事件循环的上下文（如 FastAPI 端点）会报错——因此 FastAPI 端点必须用 `await adapter.*_async(...)`（见 Step 7），Celery worker 同步上下文用 `asyncio.run`。

---

### Step 3: MinIO 上传 helper + 适配器产物落盘

**新建**：`project/backend/ai_generation/adapters/_minio_helper.py`

```python
import io
import uuid
from config_manager import config_manager  # 或 from common_sdk.config import config_manager

def upload_bytes(data: bytes, object_prefix: str, content_type: str) -> str:
    """上传字节到 MinIO，返回 object_name（不返 URL，URL 按需预签名）。"""
    from db_clients.minio import get_minio_client
    bucket = config_manager.get("MINIO_BUCKET", "prodvideofactory")
    object_name = f"{object_prefix}/{uuid.uuid4().hex}"
    get_minio_client().upload_file(bucket, object_name, data, content_type)
    return f"{bucket}/{object_name}"

def presigned_url(object_name_with_bucket: str, expires: int = 3600) -> str:
    bucket, obj = object_name_with_bucket.split("/", 1)
    return get_minio_client().get_presigned_url(bucket, obj, expires)
```

**改动各云适配器**：
- `image_dalle.py:generate_async`：拿到 `image_urls`（OpenAI URL）后，`httpx.get` 下载每个为 bytes，`upload_bytes(img_bytes, f"generated/images/{tenant}/{pipeline_id}", "image/png")`，返回 `{"image_urls": [minio_object_names]}`（注意：返 object_name 而非 URL，调用方按需预签名——避免 URL 过期，符合 plan 风险对策）。
- `tts_azure.py:synthesize_async`：`resp = await client.post(...)` 后 `resp.raise_for_status()`，然后 `audio_bytes = resp.content`，`upload_bytes(audio_bytes, f"generated/audio/{tenant}/{pipeline_id}", "audio/mpeg")`，返回 `{"audio_object": "prodvideofactory/generated/audio/.../xxx"}`。
- `video_veo3.py` / `video_sora.py`：见 Step 4，轮询拿到视频 URL 后下载上传。

**`tenant` / `pipeline_id` 来源**：适配器方法签名加可选参数 `tenant_id: str = "default"`、`pipeline_id: str = ""`，由 `tasks.py` 传入（从 `self.redis_client` 或任务参数取）。同步包装方法（Step 2）相应加参数。

**为什么**：消除缺陷 #7、#10。MinIO 是产物唯一权威存储，云 URL 视为临时下载源。

---

### Step 4: Veo3/Sora 长任务轮询

**文件**：`adapters/video_veo3.py`、`adapters/video_sora.py`

**Veo3 改动**：`generate_async` 内 POST predictLongRunning 拿到 `name`（操作 ID）后，循环 `GET {endpoint}/v1/operations/{name}` 直到 `response.done == true`，从 `response.response["videos"][0]["uri"]` 取视频 URL，`httpx.get` 下载 bytes，`upload_bytes(..., "generated/videos/{tenant}/{pipeline_id}", "video/mp4")`。超时 600 秒，指数退避（初始 10s，×1.5，上限 30s）。返回 `{"clip_objects": [minio_object_names]}`。

**Sora 改动**：`generate_async` 内 POST `/v1/video/generations` 拿到 `id` 后，循环 `GET {endpoint}/v1/video/generations/{id}` 直到 `status == "completed"`，从 `videos[0].url` 取视频 URL，下载上传 MinIO。同样超时 600s + 指数退避。

**进度上报**：轮询循环内 `redis_client.hset(f"task:{task_id}", mapping={"progress_percent": str(10 + int(80 * elapsed / 600))})`。需在 `generate_async` 签名加 `task_id: str | None = None` 参数，`tasks.py` 传入。

**为什么**：消除缺陷 #8。架构 §3.1 视频接口为异步 task_id 模式，但 ai-generation 内部 `/internal/video/generate` 端点应同步等待结果（架构 §3.1 LLM 同步、Image/Video/TTS 返 task_id 是 MCP/外部视角；内部 Celery 任务应完成全流程后返最终 URL）。决策：Celery 任务内全流程完成（含轮询），对外返 `clip_objects`。

---

### Step 5: ComfyUI 真实工作流

**文件**：`adapters/image_comfyui.py:_build_workflow`

**改动**：返回真实 ComfyUI API JSON：

```python
def _build_workflow(self, prompts, size, n, negative_prompt, seed):
    parts = size.split("x")
    width = int(parts[0]) if len(parts) == 2 else 1024
    height = int(parts[1]) if len(parts) == 2 else 1024
    actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": actual_seed, "steps": 20, "cfg": 7,
            "sampler_name": "euler", "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0],
            "negative": ["7", 0], "latent_image": ["5", 0],
        }},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {
            "ckpt_name": self.capabilities.get("ckpt", "sd_xl_base_1.0.safetensors"),
        }},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": n}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompts[0] if prompts else "", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt or "", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }
```

并改 `generate_async`：POST `/prompt` 拿 `prompt_id` 后，循环 `GET /history/{prompt_id}` 直到出现，从 `outputs.9.images[0].filename` 取文件名，`GET /view?filename=...` 下载 bytes，`upload_bytes(..., "generated/images/{tenant}/{pipeline_id}", "image/png")`，返回 `{"image_objects": [...]}`。

**为什么**：消除缺陷 #9。ComfyUI API 是节点图 JSON，空 `prompt` 会 400。

---

### Step 6: Vault 凭证注入

**文件**：所有 7 个适配器

**改动**：每个 `*_async` 方法内的 `api_key = os.getenv(...)` 替换为：

```python
from common_sdk.vault_client import vault_client
cred = vault_client.get_model_credential(self.adapter_id)
api_key = cred.get("api_key", "") if cred else ""
```

**为什么**：消除缺陷 #6。`vault_client` 在 `VAULT_ENABLED=false` 时已自动 fallback 到 env（`_ENV_MAP`），单一 API。生产环境开启 Vault 后自动切换，无需改代码。

---

### Step 7: `routes.py` 复用 lifespan `_router_service`

**文件**：`project/backend/ai_generation/routes.py` + `main.py`

**改动**：
1. `main.py` 顶部新增 `_router_service: ModelRouterService | None = None`（已有，line 40）。
2. `routes.py` 新增依赖：

```python
def get_router_service() -> ModelRouterService:
    from .main import _router_service
    if _router_service is None:
        raise ServiceException("Router service not initialized")
    return _router_service
```

3. 5 个 `/internal/*` 端点 + `/models` 端点：删 `reg_manager = RegistryManager(); reg_manager.register_default_adapters(); router_svc = ModelRouterService(...)` 三行，改 `router_svc: ModelRouterService = Depends(get_router_service)`。
4. 端点内同步调用 `adapter.chat(...)` 改 `await adapter.chat_async(...)`（端点已是 `async def`，复用事件循环）。同理 `adapter.generate(...)` → `await adapter.generate_async(...)`，`adapter.synthesize(...)` → `await adapter.synthesize_async(...)`。

**为什么**：消除缺陷 #3。lifespan 单例复用，避免每请求重读 Nacos + 重建 7 个适配器。`/metrics` 端点同理改。

---

### Step 8: `tasks.py` 4 任务接 router + MinIO + 内容安全

**文件**：`project/backend/ai_generation/tasks.py` + 新建 `worker_router.py`

**新建** `project/backend/ai_generation/worker_router.py`：

```python
from .registry_manager import RegistryManager
from .router import ModelRouterService

_worker_router: ModelRouterService | None = None

def get_worker_router() -> ModelRouterService:
    global _worker_router
    if _worker_router is None:
        reg = RegistryManager()
        reg.register_default_adapters()
        _worker_router = ModelRouterService(reg.registry)
    return _worker_router
```

**改动 `tasks.py`** 4 任务：

#### `generate_copywriting_task`
```python
from .worker_router import get_worker_router
from common_sdk.exceptions import ServiceException
# ... 删 mock text 拼接
router = get_worker_router()
adapter = router.route_llm(preferred_model=model, product_tier="normal")
if adapter is None:
    raise ServiceException("No healthy LLM adapter available")
try:
    messages = [{"role": "system", "content": "你是电商文案专家"},
                {"role": "user", "content": f"商品：{product_title}。描述：{product_desc or ''}。关键词：{','.join(keywords)}。风格：{style}。限 {max_length} 字。"}]
    result = asyncio.run(adapter.chat_async(messages=messages, max_tokens=max_length, temperature=0.7))
    text = result["text"]
    adapter.mark_success()
except Exception as e:
    router.router.track_failure(adapter.adapter_id)
    raise
# 内容安全（已有，保留）
safety = content_safety_client.check_text(text)
# ... 写 Redis result
```

#### `generate_images_task`
```python
router = get_worker_router()
adapter = router.route_image(preferred_model=model, product_tier="normal")
if adapter is None:
    raise ServiceException("No healthy Image adapter available")
try:
    result = asyncio.run(adapter.generate_async(
        prompts=prompts, size=size, n=n,
        negative_prompt=negative_prompt, seed=seed,
        tenant_id=tenant_id, pipeline_id=task_id,
    ))
    image_objects = result.get("image_objects", [])
    adapter.mark_success()
except Exception as e:
    router.router.track_failure(adapter.adapter_id)
    raise
# 内容安全：每个 image_object 预签名 URL 后 check_image（已有逻辑，改 URL 来源）
# 写 Redis result: {"image_objects": [...]}
```

#### `generate_video_clips_task`
```python
router = get_worker_router()
adapter = router.route_video(preferred_model=model, product_tier="normal")
if adapter is None:
    raise ServiceException("No healthy Video adapter available")
try:
    result = asyncio.run(adapter.generate_async(
        type=video_type, prompts=prompts,
        reference_image_url=reference_image_url,
        duration=duration, resolution=resolution, count=count,
        motion_strength=motion_strength,
        tenant_id=tenant_id, pipeline_id=task_id, task_id=task_id,
    ))
    clip_objects = result.get("clip_objects", [])
    adapter.mark_success()
except Exception as e:
    router.router.track_failure(adapter.adapter_id)
    raise
# 写 Redis result: {"clip_objects": [...]}
```

#### `tts_synthesize_task`
```python
router = get_worker_router()
adapter = router.route_tts(preferred_model=None)
if adapter is None:
    raise ServiceException("No healthy TTS adapter available")
try:
    result = asyncio.run(adapter.synthesize_async(
        text=text, voice=voice, language=language, speed=speed,
        tenant_id=tenant_id, pipeline_id=task_id,
    ))
    adapter.mark_success()
except Exception as e:
    router.router.track_failure(adapter.adapter_id)
    raise
# 写 Redis result: {"audio_object": "..."}
```

**任务签名补 `tenant_id`**：4 个任务都加 `tenant_id: str = "default"` 参数（`routes.py` 传 `req` 中的 tenant，或从 JWT payload 取）。

**为什么**：消除缺陷 #2。任务层完全去 Mock，接真实 router + 适配器。

---

### Step 9: 修 CostCalculator 日志端点

**文件**：`utils/model_adapters/cost.py` + 各适配器调用处

**改动**：`CostCalculator.log_usage` 当前 `httpx.post(target, ...)` 发到 `self.endpoint + /api/v1/internal/usage/log`（云 URL，错）。改为**直接写 MySQL**：

```python
def log_usage(self, usage_record: UsageRecord) -> None:
    try:
        from db_clients.mysql import get_mysql_client
        import asyncio
        mysql = get_mysql_client()
        # 同步上下文调异步 MySQL 客户端
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        sql = """INSERT INTO model_usage_log
            (adapter_id, adapter_type, model, pipeline_id, tenant_id,
             input_tokens, output_tokens, image_count, duration_seconds,
             estimated_cost, status, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())"""
        params = (usage_record.adapter_id, usage_record.adapter_type, usage_record.model,
                  usage_record.pipeline_id, usage_record.tenant_id,
                  usage_record.input_tokens, usage_record.output_tokens,
                  usage_record.image_count, usage_record.duration_seconds,
                  usage_record.estimated_cost_usd, usage_record.status)
        if loop and loop.is_running():
            asyncio.ensure_future(mysql.execute(sql, params))
        else:
            asyncio.run(mysql.execute(sql, params))
    except Exception:
        pass  # 计量失败不阻断主流程
```

各适配器的 `self._cost_calc.log_usage(record, self.endpoint)` 调用：删第二参数 `self.endpoint`。

**为什么**：消除缺陷 #11。直接写 MySQL 避免端点混淆，且 `model_usage_log` 表已在 `main.py:90 _ensure_usage_log_table` 创建。Celery worker 也用同一 `get_mysql_client()` 单例。

---

### Step 10: `requirements.txt` 补充

已有 `ffmpeg-python`、`moviepy`、`playwright`、`nacos-sdk-python`、`hvac`、`alibabacloud-green20220302`、`cryptography`（Phase 1 已加）。Phase 2 无新依赖。

---

## Assumptions & Decisions

1. **真实云调用**：用户选"全真实实现"。适配器 WILL 调 OpenAI/Anthropic/Google/Azure/ComfyUI 真实 API。单测用 `httpx.MockTransport` mock；集成测需真实 API key（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_ACCESS_TOKEN` / `AZURE_SPEECH_KEY`）。
2. **Vault fallback**：`VAULT_ENABLED=false` 时 `vault_client.get_model_credential` 自动 fallback 到 env（`_ENV_MAP` 已映射 7 个 adapter_id）。生产开 Vault 后零代码改动切换。
3. **MinIO 必须可用**：适配器产物必须落 MinIO。MinIO 不可用时适配器抛异常（无 fail-soft——产物无 URL 可返）。`main.py` lifespan 已 `minio_client.connect()`。
4. **视频长任务超时**：Veo3/Sora 轮询上限 600s。Celery 任务 `time_limit=1800`、`soft_time_limit=1500`（在 `@celery_app.task` 装饰器加）。进度写 Redis `task:{task_id}.progress_percent` 防僵尸。
5. **同步 vs 异步**：FastAPI 端点 `async def` → `await adapter.*_async(...)`；Celery 任务同步 → `asyncio.run(adapter.*_async(...))`。云适配器同步包装方法（Step 2）仅作 fallback，不被主流程用。
6. **CostCalculator**：从 HTTP POST 改直接 MySQL 写。`model_usage_log` 表已 ensured。
7. **Worker router 单例**：Celery worker 不跑 FastAPI lifespan → `worker_router.get_worker_router()` 模块级单例，首次调用时构建。
8. **产物返 object_name 不返 URL**：MinIO 预签名 URL 会过期。适配器返 `{"image_objects": ["prodvideofactory/generated/images/.../xxx"]}`，查询时 `routes.py` 的 `/internal/tasks/{task_id}/result` 端点按需 `presigned_url(obj)`。但 Phase 2 范围内，`tasks.py` 写 Redis 结果时直接存 object_name；`task_manager.get_task_result` 暂不改（保持向后兼容，Phase 6 再优化）。
9. **`tasks.py` 内容安全**：copywriting `check_text`、images `check_image` 已有（Phase 1 埋点），保留。video 不在 ai-generation 检（在 publish_dispatcher 检）。TTS 不检。
10. **`tenant_id` 透传**：4 个 Celery 任务签名加 `tenant_id` 参数，`routes.py` `send_task` 时从 `request.state.tenant_id`（JWT 注入）传入。

---

## Verification Steps

### Phase 1 收尾验证
1. `pytest tests/test_auth_unified.py -v` → 9/9 PASSED。

### Phase 2 验证

**静态验证**：
2. `python -c "from project.backend.ai_generation.registry_manager import RegistryManager; rm = RegistryManager(); rm.register_default_adapters(); ads = rm.registry.list_adapters(); print(len(ads), [type(a).__name__ for a in ads])"` → 7 个适配器，类型为 `OpenAILLMAdapter` / `ClaudeLLMAdapter` / `ComfyUIImageAdapter` / `DALLEImageAdapter` / `Veo3VideoAdapter` / `SoraVideoAdapter` / `AzureTTSAdapter`（非 `BaseModelAdapter`）。
3. `python -c "from project.backend.ai_generation.adapters import DALLEImageAdapter; a = DALLEImageAdapter('dalle3'); print(hasattr(a, 'generate'), hasattr(a, 'generate_async'))"` → `True True`。
4. `python -c "from project.backend.ai_generation.adapters import ComfyUIImageAdapter; a = ComfyUIImageAdapter('comfyui_sdxl'); w = a._build_workflow(['a cat'], '1024x1024', 1, None, 42); print('KSampler' in str(w), 'CheckpointLoaderSimple' in str(w))"` → `True True`。

**单元测试（新建 `tests/test_ai_generation_phase2.py`）**：
5. 用 `httpx.MockTransport` 模拟 OpenAI `/v1/chat/completions` 返 200 + choices，断言 `OpenAILLMAdapter.chat_async` 返 `{"text": ...}` 且 `mark_success`。
6. 模拟 OpenAI `/v1/images/generations` 返 URL，再模拟图片 bytes 下载，mock `MinioClient.upload_file`，断言 `DALLEImageAdapter.generate_async` 返 `{"image_objects": ["prodvideofactory/generated/images/.../..."]}`。
7. 模拟 Veo3 predictLongRunning 返 `{"name": "op123"}`，模拟 `GET /v1/operations/op123` 第一次 `done=false`、第二次 `done=true` + video uri，mock 下载 + MinIO，断言 `Veo3VideoAdapter.generate_async` 返 `{"clip_objects": [...]}` 且总耗时 < 5s（用 `asyncio.sleep` patch 加速）。
8. 模拟 Azure TTS 返 audio bytes，mock MinIO，断言 `AzureTTSAdapter.synthesize_async` 返 `{"audio_object": "prodvideofactory/generated/audio/..."}`。
9. `registry_manager._build_real_adapter({"id":"unknown","type":"foo","protocol":"bar"})` 抛 `ValueError`。
10. `CostCalculator.log_usage` 用 mock MySQL 客户端断言 `INSERT INTO model_usage_log` 被调用，参数正确。

**集成验证（需真实 API key + Docker 基础设施）**：
11. `docker-compose up -d mysql redis minio rabbitmq`，`celery -A project.backend.ai_generation.tasks worker -Q ai_queue` 启动，`python -m project.backend.ai_generation.main` 启动。
12. 设 `OPENAI_API_KEY=sk-...`，`POST /api/v1/copywriting` 提交，`redis-cli HGETALL task:copywriting_xxx` 看 `status=completed`、`result` 含真实 OpenAI 返文本。
13. `POST /api/v1/images/generate` 提交，MinIO 控制台 `generated/images/...` 见对象，`redis-cli HGETALL task:image_xxx` 看 `image_objects`。
14. 故障注入：`OPENAI_API_KEY=invalid`，连续 3 次 `POST /api/v1/copywriting`，第 4 次 `GET /api/v1/models?type=llm` 见 `openai_gpt4o.is_healthy=false`，路由降级到 `claude_sonnet`（若 ANTHROPIC_API_KEY 有效）。
15. 内容安全：`CONTENT_SAFETY_ENABLED=true` + `ALIBABA_CLOUD_ACCESS_KEY_ID=...`，提交违规文案，`redis-cli HGETALL task:copywriting_xxx` 看 `status=content_filtered`。
16. `SELECT adapter_id, status, estimated_cost FROM model_usage_log ORDER BY id DESC LIMIT 5` 见真实用量记录。

---

## 关键文件清单

**Phase 1 收尾**：
- `project/backend/product_analyzer/auth.py`（改 import）
- `project/backend/publish_dispatcher/auth.py`（改 import）

**Phase 2 主体**：
- `project/backend/ai_generation/registry_manager.py`（`_build_real_adapter` + `_ADAPTER_MAP`）
- `project/backend/ai_generation/adapters/llm_openai.py`（删重复 sync `chat`，改 `asyncio.run` 包装）
- `project/backend/ai_generation/adapters/llm_claude.py`（同上）
- `project/backend/ai_generation/adapters/image_dalle.py`（加 sync `generate`、MinIO 上传、Vault 凭证）
- `project/backend/ai_generation/adapters/image_comfyui.py`（加 sync `generate`、真实 workflow、轮询 history、MinIO 上传、Vault）
- `project/backend/ai_generation/adapters/video_veo3.py`（加 sync `generate`、operations 轮询、MinIO 上传、Vault）
- `project/backend/ai_generation/adapters/video_sora.py`（加 sync `generate`、status 轮询、MinIO 上传、Vault）
- `project/backend/ai_generation/adapters/tts_azure.py`（加 sync `synthesize`、保存 `resp.content` 到 MinIO、Vault）
- `project/backend/ai_generation/adapters/_minio_helper.py`（新建，`upload_bytes` + `presigned_url`）
- `project/backend/ai_generation/routes.py`（5 端点改 `Depends(get_router_service)` + `await *_async`）
- `project/backend/ai_generation/main.py`（`_router_service` 已有，无需改；`/metrics` 端点改用 `get_router_service`）
- `project/backend/ai_generation/tasks.py`（4 任务接 `get_worker_router` + `asyncio.run(*_async)` + 内容安全保留）
- `project/backend/ai_generation/worker_router.py`（新建，`get_worker_router` 单例）
- `utils/model_adapters/cost.py`（`log_usage` 改直接 MySQL 写，删 endpoint 参数）

**测试**：
- `tests/test_ai_generation_phase2.py`（新建，10 个单测覆盖 Step 1-9）
