# ProdVideo AI Factory 深度开发方案

## Context（为什么做这个改动）

项目骨架完整（8 微服务 + MCP 网关 + Vue3 前端 + Docker 基础设施），但深度审计发现多处 Mock/Stub/断裂，导致架构文档 V4.1 描述的端到端流水线无法真正运行：

- **ai-generation 任务层 100% Mock**：`tasks.py` 4 个 Celery 任务返假 URL（`storage.prodvideo.local/...`），从未调用适配器。
- **registry_manager 造 stub**：`_build_adapter_stub` 用 `BaseModelAdapter.__new__` 造裸对象，未实例化 `adapters/` 下真实云适配器子类，适配器层与任务层脱钩。
- **video-composer 纯空壳**：无 FFmpeg，返假 URL。
- **采集/发布连接器零真实实现**：`utils/platform_connectors/` 仅框架，无 douyin/taobao/amazon/shopee 实现，`crawl_scheduler/connectors/__init__.py` 空文件。
- **事件驱动断链**：`product:hot_score_changed` 发布端存在，订阅端缺失。
- **基础设施缺口**：`verify_internal_jwt` 三份重复变体、启动路径错误、Nacos/Vault/内容安全未接入、MinIO 封装无消费方。

用户决策：**全真实实现** + **先修基础设施与 Bug**，授权直接跟进后续所有阶段。本方案分 6 阶段，Phase 1 详细到可执行，Phase 2-6 给路线图。

---

## Phase 1：基础设施与 Bug 修复（可执行级）

### 1.1 下沉 `verify_internal_jwt` 到 common_sdk

**现状**：`utils/common_sdk/auth.py` 仅有 `decode_service_jwt`，无 FastAPI 依赖版本；`product_analyzer/auth.py`、`crawl_scheduler/auth.py`、`ai_generation/auth.py`、`publish_dispatcher/auth.py` 四份变体重复。

**改动**：
- 在 `utils/common_sdk/auth.py` 新增 `async verify_internal_jwt(request, authorization=Header(None), x_tenant_id=Header(None, alias="X-Tenant-ID")) -> dict`：复用 `decode_service_jwt` + `config_manager.get("INTERNAL_JWT_SECRET")`，失败抛 `AuthException`，成功后 `request.state.tenant_id` / `request.state.service_name` 注入。
- 四个服务 `auth.py` 改为 `from utils.common_sdk.auth import verify_internal_jwt`（re-export，保留旧名作兼容别名）。`ai_generation/routes.py` 的 `Depends(verify_jwt)` 统一为 `Depends(verify_internal_jwt)`；`publish_dispatcher` 抛 `HTTPException` 路径改抛 `AuthException`。

**复用**：`decode_service_jwt`、`config_manager`、`AuthException`（`utils/common_sdk/exceptions.py`）。

### 1.2 修复启动路径

- `project/backend/product_analyzer/main.py:156`：`"main:app"` → `"project.backend.product_analyzer.main:app"`
- `project/backend/crawl_scheduler/main.py:103`：`"crawl_scheduler.main:app"` → `"project.backend.crawl_scheduler.main:app"`
- 参考 `ai_generation/main.py:182` 已正确格式。

### 1.3 接入 Nacos 配置加载

**新建** `utils/common_sdk/nacos_client.py`：
- `NacosConfigProvider` 单例：`connect(server_addr, namespace, group)` / `get_yaml(data_id, default=None)` / `get_config(data_id, default="")` / `add_watcher(data_id, callback)`。
- 懒加载 `import nacos`；`NACOS_ENABLED=false` 或连接失败时 `get_yaml` 直接返回 `default`，不抛异常。模块级 `nacos_provider`。

**新建** `utils/common_sdk/registry_config.py`（配置加载门面）：
- `load_model_registry() -> dict[str, list[dict]]`：`nacos_provider.get_yaml("model-adapters.yaml", default=_MODEL_REGISTRY_FALLBACK)`，fallback 即当前 `registry_manager._adapters_config` 内容（迁移为常量）。
- `load_platform_connectors() -> list[PlatformAdapterConfig]`：从 `platform-connectors.yaml` 加载，fallback 为当前 `crawl_scheduler/routes.py:31-56` 的 4 个平台。

**改动**：
- `ai_generation/registry_manager.py`：删 `_adapters_config` 硬编码，`load_adapters()` 改调 `load_model_registry()`。Phase 1 仅切配置来源，stub 构造留 Phase 2 替换。
- `crawl_scheduler/routes.py`：删 `PLATFORM_CONNECTORS` 硬编码，`list_platforms` 改调 `load_platform_connectors()`。
- `ai_generation/config.py`：新增 `NACOS_ENABLED = config_manager.get_bool("NACOS_ENABLED", True)`。

### 1.4 接入 HashiCorp Vault 客户端框架

**新建** `utils/common_sdk/vault_client.py`：
- `VaultClient` 单例：`connect(url, token, mount="secret")` / `read_secret(path)` / `write_secret(path, data)` / `delete_secret(path)`；业务语义 `get_platform_refresh_token(platform, tenant)` / `store_platform_refresh_token(...)` / `get_model_credential(adapter_id)`（解析 `auth_ref: vault:veo3_credentials`）。
- 懒加载 `import hvac`；`VAULT_ENABLED=false` 或连接失败时：`get_platform_refresh_token` fallback 到 MySQL `platform_authorizations` 表；`get_model_credential` fallback 到 env（`OPENAI_API_KEY`/`GOOGLE_ACCESS_TOKEN` 等）。模块级 `vault_client`。
- 路径约定（架构 7.3）：`secret/data/platforms/{platform}/{tenant}`、`secret/data/models/{adapter_id}`。

**改动**：
- `docker-compose.yml`：新增 `vault` 服务（`hashicorp/vault:1.15` dev 模式，端口 8200）。
- `.env`：补 `VAULT_ENABLED`、`VAULT_ADDR`、`VAULT_TOKEN`、`VAULT_MOUNT`。

**复用**：`config_manager`、`get_logger`、`MySQLClient`（fallback 读 token）。

### 1.5 接入内容安全 API（阿里云绿网，架构 7.4）

**新建** `utils/common_sdk/content_safety.py`：
- `ContentSafetyResult` dataclass：`passed: bool` / `risk_level: str`(none/low/medium/high) / `detail: str`。
- `ContentSafetyClient` 单例：`check_text(text)` / `check_image(image_url)` / `async check_video_async(video_url)`。懒加载 `alibabacloud_green20220302` 或 httpx；`CONTENT_SAFETY_ENABLED=false` 时默认 **fail-open**（`passed=True` + warning），`CONTENT_SAFETY_FAIL_CLOSED=true` 切 fail-closed。模块级 `content_safety_client`。

**改动**：
- `utils/common_sdk/exceptions.py`：新增 `ContentFilteredException(code=451, http_status=451)`。
- `ai_generation/tasks.py`：Phase 1 埋点——文案任务返回前 `check_text`，图片任务 `check_image`，不通过则写 Redis `status=content_filtered` 并抛 `ContentFilteredException`。Phase 2 接真实生成后生效。
- `publish_dispatcher/tasks.py`：发布前 `check_video_async`。

**复用**：`config_manager`、`get_logger`、`MinioClient`（取预签名 URL 供检测）。

### 1.6 更新 requirements.txt

根 `requirements.txt` 新增：`nacos-sdk-python>=0.1.13`、`hvac>=2.3.0`、`ffmpeg-python>=0.2.0`、`moviepy>=1.0.3`、`playwright>=1.40.0`、`alibabacloud-green20220302>=3.0.0`、`cryptography>=42.0.0`。各服务 Dockerfile（若存在）补 `playwright install chromium` + 系统依赖（`libnss3` 等）+ `ffmpeg` 二进制。

### 1.7 更新 development_plan.md 进度

勾选 0.1 Docker Compose、0.1 Nacos 配置模板、0.2 common-sdk（JWT 统一）、0.2 platform-connectors（基类就绪）；"当前进度"段标 `[x] 阶段 0 基础设施`。

### Phase 1 验证
- 新增 `tests/test_auth_unified.py`：构造合法/过期/篡改 JWT，断言 `verify_internal_jwt` 行为 + `request.state.tenant_id` 注入。
- `python -m project.backend.product_analyzer.main` / `python -m project.backend.crawl_scheduler.main` 能起 uvicorn。
- `NACOS_ENABLED=false` 启动：`GET /api/v1/models` 返 fallback 适配器，`GET /api/v1/crawl/platforms` 返 4 平台。
- `VAULT_ENABLED=false`：`vault_client.get_model_credential("veo3")` 返 env 值。
- `CONTENT_SAFETY_ENABLED=false`：任务正常返回（fail-open）。
- `pip install -r requirements.txt` 成功；`python -c "import nacos, hvac, ffmpeg, moviepy, playwright"` 通过。

---

## Phase 2：ai-generation 任务层接适配器（路线图）

**目标**：消除任务层 Mock，打通"路由 → 真实云适配器 → MinIO 落盘 → Redis 结果"。

**关键文件**：`ai_generation/registry_manager.py`、`ai_generation/tasks.py`、`ai_generation/adapters/*.py`（7 个）、`ai_generation/routes.py`、`ai_generation/main.py`、`ai_generation/router.py`。

**核心策略**：
1. **修 stub**：`registry_manager._build_adapter_stub` 改为 `_build_real_adapter(cfg)`，按 `protocol` 映射到真实子类（`comfyui_api→ComfyUIImageAdapter`、`openai_rest→OpenAILLMAdapter/DALLEImageAdapter/SoraVideoAdapter`、`google_vertex→Veo3VideoAdapter`、`anthropic_rest→ClaudeLLMAdapter`、`azure_cognitive→AzureTTSAdapter`），密钥从 `vault_client.get_model_credential` 注入。**ai-generation 内禁用 `utils.model_adapters` 内部 REST 桩**。
2. **统一同步/异步契约**：云适配器同步 `chat/generate/synthesize` 内部 `asyncio.run(self.*_async())`，单一真实实现；`routes.py` `/internal/*` 改 `await adapter.*_async` 并复用 `main.py` lifespan 的 `_router_service` 单例（不再每请求重建）。
3. **任务层接 router**：`tasks.py` 4 任务调 `ModelRouterService.route_llm/image/video/tts`，无模型时按 `product_tier` 自动选；失败 `router.track_failure`，连续 3 次降级 5 分钟。
4. **产物落 MinIO**：云适配器拿到字节流后 `get_minio_client().upload_stream(bucket, "generated/{type}/{tenant}/{pipeline_id}/...", ...)` 返预签名 URL。补 `video_veo3`/`video_sora` 长任务轮询（`GET operations/{id}` 循环至 `done`，带超时退避）；补 `image_comfyui._build_workflow` 真实 API JSON（CheckpointLoader→CLIPTextEncode→KSampler→VAEDecode→SaveImage）；补 `tts_azure` 保存 `resp.content`。
5. **内容安全生效**：文案/图片任务返回前调 `content_safety_client`。

**依赖**：Phase 1。

---

## Phase 3：video-composer 真实 FFmpeg 合成（路线图）

**目标**：`compose_video_task` 真实多轨道合成，产物落 MinIO。

**关键文件**：`video_composer/tasks.py`、`config.py`、`main.py`；新建 `video_composer/composer.py`；`asset_manager/routes.py`（`/assets/adapt` 复用）。

**核心策略**：
1. `ffmpeg-python` 构造 filter graph：`video_clips` concat + `images` overlay + `audio_url` BGM（`amix`）+ 字幕压制（`subtitles` filter，由 `subtitle_text` 生成 SRT/ASS 临时文件）。
2. 模板驱动：从 MinIO `templates/{template_id}.json` 读时间轴，`composer.py` 按 JSON 生成 ffmpeg 命令。
3. 输入从 MinIO 预签名 URL 下载到 worker 临时目录（或 ffmpeg `-i` 直读），合成后 `upload_file` 到 `final/{tenant}/{pipeline_id}/output.mp4`。
4. 进度回调：解析 ffmpeg stderr `time=` 行算百分比，写 `task:{id}` hash。

**依赖**：Phase 2（素材 URL 真实可下载）。

---

## Phase 4：crawl-scheduler 真实连接器（路线图）

**目标**：填充 `CrawlerRegistry`，实现 Amazon/Shopee API 直连 + 抖音/淘宝 Playwright 渲染，对接 MongoDB `platform_mappings`。

**关键文件**：`crawl_scheduler/connectors/__init__.py`（当前空）；新建 `connectors/{amazon,shopee,douyin,taobao}.py`；`crawl_scheduler/tasks.py`、`routes.py`；`utils/platform_connectors/mapper.py`、`registry.py`。

**核心策略**：
1. `amazon.py`/`shopee.py`（`APIDirectCrawler` 子类）调开放 API（SignV4 / Shopee Open API）；`douyin.py`/`taobao.py`（`RenderCrawler` 子类）用 Playwright 启 chromium，经代理池渲染搜索页抽商品 JSON。
2. `connectors/__init__.py` 注册 4 连接器到 `CrawlerRegistry`，`main.py` lifespan 调 `registry.load_from_config(load_platform_connectors())`；`routes.py` 的 `connector_class` 字符串改为 `registry.get_crawler(platform_id)` 实例查询。
3. `tasks.py` 改调 `registry.get_crawler(platform).run_crawl(request)`，结果经 `PlatformDataMapper` 标准化：`mapper.py` 新增 `load_mappings_from_mongo()`，从 MongoDB `platform_mappings` 读 JsonPath 规则替代内存默认映射。
4. 分布式锁：`BasePlatformCrawler.acquire_crawl_lock` 接 `RedisClient.acquire_lock`（`lock:crawl:{platform}:{keyword}`）。
5. 标准化后写 MySQL `products` 表。

**依赖**：Phase 1（Nacos 连接器配置）、MongoDB `platform_mappings` 种子数据。

---

## Phase 5：publish-dispatcher 真实发布适配器（路线图）

**目标**：YouTube/TikTok/Instagram 真实发布，OAuth token 经 Vault 管理。

**关键文件**：`publish_dispatcher/tasks.py`、`routes.py`、`auth.py`；新建 `publish_dispatcher/publishers/{youtube,tiktok,instagram}.py`；`utils/platform_connectors/base_publisher.py`、`registry.py`；`asset_manager/routes.py`。

**核心策略**：
1. `publishers/*.py` 实现 `publish(request)`：`vault_client.get_platform_refresh_token` → 调平台 token 刷新接口换 access_token（缓存 Redis `oauth_token:{platform}:{tenant}`）→ 上传视频/封面（YouTube Data API resumable、TikTok Video API、Instagram Graph API）→ 设标题/标签/商品挂载 → 支持定时发布。
2. `tasks.py` 改调 `PublisherRegistry.get_publisher(platform).publish(req)`；`routes.py` 先调 `asset-manager` `/assets/adapt` 拿平台适配视频 URL，再 `send_task` 到 `publish_queue`。
3. OAuth 流：`web_backend` 提供 `/platforms/{platform}/oauth/callback`，回调后 `vault_client.store_platform_refresh_token`，`platform_authorizations` 表记录授权状态。
4. 结果写 `publish_log` 表，Redis 通知 MCP。

**依赖**：Phase 1（Vault）、Phase 3（asset-manager 适配）。

---

## Phase 6：事件驱动与端到端（路线图）

**目标**：补 `product:hot_score_changed` 订阅端，打通自动流水线，web-backend dashboard 真实聚合。

**关键文件**：`ai_generation/main.py`；新建 `ai_generation/pipeline_orchestrator.py`；`web_backend/routes.py:26-40`；`product_analyzer/tasks.py:174`（发布端已存在）。

**核心策略**：
1. ai-generation lifespan 启动后台 `asyncio.create_task` 订阅 Redis `product:hot_score_changed`（`RedisClient.subscribe`），收到事件且 `score>阈值` 时，`pipeline_orchestrator` 用 `group_tasks(generate_copywriting, generate_images, generate_video_clips)` + `chord_tasks`→`compose_video`→`publish_to_authorized_platforms` 编排，状态写 `generation_pipelines` 表。
2. `web_backend/routes.py` 的 `get_dashboard` 改真实聚合：MySQL `products` count、`generation_pipelines` active count、`publish_log` count、`model_usage_log` SUM(estimated_cost) GROUP BY model，替换 `random.randint`。
3. MCP `query_task_status` 验证可查全链路任一 task_id。

**依赖**：Phase 2-5 全部就绪。

---

## 端到端验证

**1. 起基础设施**
```
docker-compose up -d mysql mongodb redis minio rabbitmq nacos vault kong
```

**2. 起各微服务 worker + api**（每服务两进程）
```
python -m project.backend.crawl_scheduler.main
celery -A project.backend.crawl_scheduler.tasks worker -Q crawl_queue
# 同理 product_analyzer / ai_generation / video_composer / publish_dispatcher / asset_manager / web_backend
celery -A project.backend.product_analyzer.main beat
```

**3. 触发流水线（两入口）**
- MCP 入口：Claude Code/Cline 连 MCP Server，调 `crawl_hot_product` → `query_task_status` 轮询 → `analyze_product` → `generate_*` → `compose_video` → `publish_content`。
- 事件驱动入口：`redis-cli PUBLISH product:hot_score_changed '{"product_id":1,"score":95,"tier":"hot",...}'`，观察 ai-generation 自动起流水线。

**4. 每步检查**
| 步骤 | 检查 |
|---|---|
| 采集 | `redis-cli HGETALL task:crawl-xxx` 看 `products_found`；MySQL `SELECT * FROM products` |
| 分析 | `redis-cli ZRANGE hot_products:daily 0 -1 WITHSCORES` |
| 文案/图/视频 | `redis-cli HGETALL task:copywriting_xxx` 看 `result`；MinIO 控制台 `generated/images/...` 有对象 |
| 合成 | MinIO `final/{tenant}/{pipeline_id}/output.mp4` 存在可播放 |
| 发布 | MySQL `SELECT * FROM publish_log`；目标平台创作者后台见草稿 |
| 全链路 | `SELECT * FROM generation_pipelines WHERE id=...` 各 stage 状态 |

**5. 故障注入**
- 停 Veo3 凭证：路由降级到 Sora，`redis-cli HGET model_status:veo3` 显示禁用。
- 断 Nacos：服务用 fallback 配置继续运行（日志 warning）。
- 安全拦截：提交违规文案，任务状态 `content_filtered`。

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| Nacos/Vault 无真实实例 | `NACOS_ENABLED`/`VAULT_ENABLED` 开关 + 代码 fallback（注册表 fallback 常量、凭证 fallback 到 env/MySQL）。Phase 1 全 fail-soft，不阻断启动。 |
| Playwright 逆向风险（抖音/淘宝风控、DOM 变更） | 连接器插件化 + 版本号；代理池 + 随机 UA + 限速（复用 `rate_limit_wait`）；失败降级返空 + 告警；预留切换官方 API 开关。 |
| API Key 缺失 | `vault_client.get_model_credential` 返 None 时，适配器 `mark_failure` + Redis `model_status:{id}` 标不可用，路由自动选下一个；全不可用返 `ServiceException`。 |
| 同步/异步契约混淆 | 云适配器同步方法统一 `asyncio.run(self.*_async())`，单一真实实现；Celery worker 每任务独立事件循环。 |
| 视频长任务超时 | 适配器内带超时 + 指数退避轮询；Celery `time_limit`/`soft_time_limit` 设宽；进度写 Redis 防僵尸。 |
| MinIO 预签名 URL 过期 | 任务结果存 object_name 而非 URL，查询时按需 `get_presigned_url(expires_seconds=3600)`。 |
| 两套同名适配器误用 | Phase 2 在 `registry_manager` 显式 `from .adapters import ...`（云实现），禁止 ai-generation 内 import `utils.model_adapters` 内部 REST 桩。 |

---

## 关键文件清单（实施时重点）

- `utils/common_sdk/auth.py`（下沉 `verify_internal_jwt`）
- `utils/common_sdk/nacos_client.py`（新建，Nacos 配置门面）
- `utils/common_sdk/vault_client.py`（新建，凭证加密存取）
- `utils/common_sdk/content_safety.py`（新建，内容安全）
- `utils/common_sdk/registry_config.py`（新建，配置加载门面）
- `project/backend/ai_generation/registry_manager.py`（修 stub，Phase 1/2 交汇点）
- `project/backend/ai_generation/tasks.py`（4 任务接 router + MinIO + 内容安全，Phase 2 核心）
- `project/backend/video_composer/tasks.py` + 新建 `composer.py`（Phase 3）
- `project/backend/crawl_scheduler/connectors/`（新建 4 连接器，Phase 4）
- `project/backend/publish_dispatcher/publishers/`（新建 3 发布器，Phase 5）
- `project/backend/ai_generation/pipeline_orchestrator.py`（新建，Phase 6 事件驱动）
- `docker-compose.yml`（加 vault）、`requirements.txt`、`.env`、`doc/development_plan.md`
