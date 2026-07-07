# Phase 9 Part 1: 安全加固实施计划

> **承接**：Phase 8（韧性模式）已完成。用户在 Phase 9 方向探索中选择"按优先级开始进行每一个方向"——本计划为 Phase 9 Part 1（安全加固），完成后依次进入 Part 2（E2E 集成测试）、Part 3（API 文档 + 生产就绪）。
>
> **目标**：消除生产部署前最危险的 7 类安全问题——JWT secret 不一致、auth 实现碎片化、9 个零鉴权端点、4 个服务跨租户查询泄漏、硬编码开发密码、路由内 DDL、RateLimiter 死代码。

---

## 一、当前状态分析

### 1.1 JWT Secret 不一致（P0 — 内部 HTTP 调用全失败）

| 文件 | 行 | 默认值 | 与 `.env` 一致？ |
|------|----|--------|-----------------|
| `utils/common_sdk/auth.py` | L58 | `dev-jwt-secret-prodvideofactory-2024` | ✅ |
| `utils/common_sdk/http_client.py` | L50, L220 | `dev-jwt-secret` | ❌ |
| `project/backend/ai_generation/config.py` | L16 | `dev-jwt-secret` | ❌ |
| `.env` | L36 | `dev-jwt-secret-prodvideofactory-2024` | — |

**后果**：未设置 `INTERNAL_JWT_SECRET` 环境变量时，`http_client` 用 `dev-jwt-secret` 签发 token，下游 `verify_internal_jwt` 用 `dev-jwt-secret-prodvideofactory-2024` 验签 → 全部 401。开发环境恰好 `.env` 设了变量，所以掩盖了 bug，但生产一旦漏配环境变量就静默崩溃。

### 1.2 Auth 实现碎片化（P0 — 5+ 套实现）

| 实现 | 问题 |
|------|------|
| `utils/common_sdk/auth.py` | **canonical**：`verify_internal_jwt` 设置 `tenant_id` + `service_name`，HS256 验签 |
| `project/backend/ai_generation/auth.py` | 别名（重复导出） |
| `project/backend/publish_dispatcher/auth.py` | 别名 |
| `project/backend/asset_manager/auth.py` | `verify_internal_request` **只设置 `service_name`，不设置 `tenant_id`** → 后续路由 `getattr(request.state, "tenant_id", "default")` 永远是 "default"；且用本地 `JWT_SECRET`（默认 `dev-jwt-secret`，与 canonical 不一致） |
| `project/backend/mcp_gateway/auth.py` | **纯字符串解析，无哈希验证，无 DB 查找**——任何人构造 `mcp_sk.<任意租户>.<任意串>` 即可冒充该租户 |
| `project/backend/web_backend/auth.py` | `verify_admin_request`（外部用户，独立体系） |

### 1.3 零鉴权端点（P0 — 9 个）

| 服务 | 端点 | 当前状态 | 风险 |
|------|------|---------|------|
| `video_composer` | `POST /api/v1/compose` | 无 Depends | 任意用户可触发视频合成（消耗 GPU/带宽） |
| `video_composer` | `GET /api/v1/compose/{task_id}` | 无 Depends | 任意 task_id 可读他人任务结果 |
| `video_composer` | `GET /api/v1/compose` | 无 Depends | 列出全部租户任务 |
| `pipeline_orchestrator` | `POST /api/v1/pipelines` | 无 Depends，`tenant_id` 从 body 取 | 客户端可伪造任意 tenant_id |
| `pipeline_orchestrator` | `GET /api/v1/pipelines/{pipeline_id}` | 无 Depends | 跨租户读 pipeline |
| `mcp_gateway` | `POST /mcp/message` | 无 Depends | 任意请求触发 MCP 工具调用 |
| `mcp_gateway` | `GET /mcp/sse` | `verify_api_key` 失败仍继续 | 无效 key 也能建立 SSE |
| `mcp_gateway` | `POST /mcp/sse/{session_id}` | 无 Depends | 任意 session_id 投递消息 |
| `mcp_gateway` | `GET /business_metrics` | 无 Depends | **豁免**（Prometheus 抓取需要公开） |

### 1.4 跨租户查询泄漏（P0 — 4 服务）

| 服务 | 端点 | SQL 缺陷 |
|------|------|---------|
| `product_analyzer` | `GET /products/{id}/score` | `WHERE id=%s` 缺 `AND tenant_id=%s` |
| `product_analyzer` | `GET /products/hot` | `WHERE id IN (...)` 缺 tenant_id（数据源是全局 Redis sorted set，需额外评估） |
| `publish_dispatcher` | `GET /publish/{task_id}` | `WHERE platform_post_id=%s` / `WHERE pipeline_id=%s` 缺 tenant_id |
| `publish_dispatcher` | `GET /publish/pipeline/{pipeline_id}` | `WHERE pipeline_id=%s` 缺 tenant_id |
| `publish_dispatcher` | `GET /platforms` | `tenant_id` 从 Query 参数取（可伪造） |
| `asset_manager` | `GET /platform-configs` | 无 tenant_id 过滤 |
| `asset_manager` | `POST /platform-configs` | 无 tenant_id 过滤，写入时也未带 tenant_id |
| `asset_manager` | `DELETE /platform-configs/{id}` | `WHERE id=%s` — 任何租户可删任何配置 |
| `pipeline_orchestrator` | `GET /pipelines/{id}` | `WHERE id=%s` 缺 tenant_id |

### 1.5 硬编码开发密码（P1）

`database/init.sql`、`docker-compose.yml`、`crawl_scheduler/routes.py` L307 等多处硬编码 `dev_redis_2024`、`dev_pass_2024`、`minioadmin2024`、`kong_dev_2024`、`nacos2024`。`.env` 已存在但缺 `.env.example` 模板。

### 1.6 路由内 DDL（P1）

`crawl_scheduler/routes.py` 在 `create_crawl_plan` (L158-174) 和 `list_crawl_plans` (L198-214) 内执行 `CREATE TABLE IF NOT EXISTS crawl_plans`——每次请求都跑一次 DDL。`database/init.sql` 已有 `crawl_plans` 表定义（L33-50），但 schema 略有差异（init.sql 用 `BIGINT AUTO_INCREMENT`，routes.py 用 `VARCHAR(64) PRIMARY KEY`）。需要统一到 init.sql。

### 1.7 RateLimiter 死代码（P2）

`utils/common_sdk/resilience.py` L285 定义 `RateLimiter` 类，全代码库零引用。应接线为 FastAPI 中间件保护公共写入端点（`POST /pipelines`、`POST /compose`、`POST /publish`、`POST /crawl/jobs`）。

---

## 二、设计决策

1. **Auth 统一方向**：所有内部服务间调用统一使用 `common_sdk.auth.verify_internal_jwt`（FastAPI dependency）。删除/重构 5 套碎片化实现。外部用户（web_backend → 浏览器）保留独立 `verify_admin_request`。
2. **MCP API key 验证**：从纯字符串解析升级为 DB 查找 + SHA-256 哈希比较（复用 `common_sdk.auth.verify_api_key` + `api_keys` 表）。`api_keys` 表 schema 已就绪。
3. **跨租户过滤策略**：所有按主键查询单条记录的 SQL 添加 `AND tenant_id=%s`，`tenant_id` 从 `request.state.tenant_id` 取（由 `verify_internal_jwt` 注入）。`products/hot` 因数据源是全局 Redis sorted set，本轮添加 tenant_id 过滤 SQL 层（hot 列表本身可跨租户，但产品详情查询不应泄漏）。
4. **DDL 处理**：将 `crawl_plans` schema 统一到 `database/init.sql`（采用 `VARCHAR(64)` 主键版本，因 routes.py 已按此使用），从 routes.py 删除 DDL。
5. **RateLimiter 中间件**：创建 `common_sdk.middleware.rate_limit_middleware`，按路径前缀配置速率。本轮仅接线到 4 个高风险写入端点，默认每秒 5 请求/突发 10。
6. **不破坏现有测试**：所有修改保持向后兼容——`verify_internal_request`（asset_manager）保留为 `verify_internal_jwt` 的薄包装，避免大规模测试改动。

---

## 三、实施步骤

### Step 45: 统一 JWT Secret 默认值（P0，30 分钟）

**文件**：
- `utils/common_sdk/http_client.py` L50, L220：`"dev-jwt-secret"` → `"dev-jwt-secret-prodvideofactory-2024"`
- `project/backend/ai_generation/config.py` L16：`"dev-jwt-secret"` → `"dev-jwt-secret-prodvideofactory-2024"`

**验证**：`pytest tests/test_phase8_http_client.py`（确认现有 httpx mock 测试不依赖具体 secret 值）。

### Step 46: 统一 Auth 实现（P0，2 小时）

**子步骤**：

**46a. 修复 `asset_manager/auth.py`**——`verify_internal_request` 改为复用 `common_sdk.auth.verify_internal_jwt`，并设置 `tenant_id`：
- 删除本地 `JWT_SECRET` 引用
- `verify_internal_request` 直接 `from utils.common_sdk.auth import verify_internal_jwt` 并别名导出
- 或保留函数签名但内部委托调用 `verify_internal_jwt`

**46b. 删除别名文件**：
- `project/backend/ai_generation/auth.py` → 删除（如存在 `from utils.common_sdk.auth import verify_internal_jwt`）
- `project/backend/publish_dispatcher/auth.py` → 同上
- 在调用处改 import 路径为 `utils.common_sdk.auth`

**46c. 升级 `mcp_gateway/auth.py`**——`verify_api_key` 改为 DB 查找 + 哈希验证：
- 接受 `api_key` 字符串
- 解析出 `tenant_id`（保留现有 `mcp_sk.<tenant>.<secret>` 格式）
- 查 `api_keys` 表：`SELECT api_key_hash, scopes, enabled, expires_at FROM api_keys WHERE tenant_id=%s AND enabled=1`
- 遍历比对 `hashlib.sha256(api_key.encode()).hexdigest() == row["api_key_hash"]`
- 返回 `{"tenant_id": ..., "scopes": ..., "api_key_prefix": ...}` 或 `None`
- 新增 FastAPI dependency `verify_mcp_api_key(request: Request)`：从 Authorization header 提取，调用上述逻辑，失败返回 401，成功设置 `request.state.tenant_id` 和 `request.state.scopes`

**验证**：`pytest tests/`（确认未破坏现有 mcp_gateway 测试——可能需要 mock MySQL）。

### Step 47: 为 8 个零鉴权端点添加 Depends（P0，1 小时）

**文件 + 变更**：

| 文件 | 端点 | 变更 |
|------|------|------|
| `video_composer/routes.py` | `POST /compose` | `async def compose(request: Request, body: ComposeRequest, _auth=Depends(verify_internal_jwt))` |
| 同上 | `GET /compose/{task_id}` | 加 `_auth=Depends(verify_internal_jwt)` |
| 同上 | `GET /compose` | 同上 |
| `pipeline_orchestrator/routes.py` | `POST /pipelines` | 加 `_auth=Depends(verify_internal_jwt)`；删除 body 中的 `tenant_id` 字段，改用 `request.state.tenant_id` |
| 同上 | `GET /pipelines/{pipeline_id}` | 加 `_auth=Depends(verify_internal_jwt)` |
| `mcp_gateway/routes.py` | `POST /mcp/message` | 加 `_auth=Depends(verify_mcp_api_key)` |
| 同上 | `GET /mcp/sse` | 加 `_auth=Depends(verify_mcp_api_key)`；移除函数体内手工 `verify_api_key` 调用 |
| 同上 | `POST /mcp/sse/{session_id}` | 加 `_auth=Depends(verify_mcp_api_key)` |
| 同上 | `GET /business_metrics` | **不加**（Prometheus 抓取豁免） |

**注意**：`pipeline_orchestrator/routes.py` 当前 `sys.path` 插入 `utils` 后 `from common_sdk...` 导入——保持该模式，import `from common_sdk.auth import verify_internal_jwt`。`video_composer` 已用 `from utils.mq_clients...` 绝对路径，统一用 `from utils.common_sdk.auth import verify_internal_jwt`。

**验证**：`pytest tests/test_pipeline_orchestrator.py tests/test_video_composer.py`（可能需要更新测试 fixture 注入 mock JWT）。

### Step 48: 修复跨租户查询泄漏（P0，2 小时）

**变更清单**：

| 文件 | 函数 | 变更 |
|------|------|------|
| `product_analyzer/routes.py` | `get_product_score` L68 | `WHERE id=%s AND tenant_id=%s`，参数 `(product_id, request.state.tenant_id)` |
| 同上 | `get_hot_products` L106 | `WHERE id IN (...) AND tenant_id=%s`，参数追加 `request.state.tenant_id` |
| `publish_dispatcher/routes.py` | `get_publish_status` L49, L54 | 两条 SELECT 都加 `AND tenant_id=%s` |
| 同上 | `get_pipeline_publish_logs` L69 | `WHERE pipeline_id=%s AND tenant_id=%s` |
| 同上 | `list_authorized_platforms` L77 | 删除 `tenant_id: str = Query("default")` 参数，改用 `request: Request` + `request.state.tenant_id` |
| `asset_manager/routes.py` | `list_platform_configs` L87 | 加 `WHERE tenant_id=%s`（无 platform 时）/ `WHERE platform=%s AND tenant_id=%s`（有 platform 时） |
| 同上 | `upsert_platform_config` L102 | INSERT 加 `tenant_id` 列；UPDATE 加 `AND tenant_id=%s` 防止跨租户改 |
| 同上 | `delete_platform_config` L122 | `WHERE id=%s AND tenant_id=%s` |
| `pipeline_orchestrator/routes.py` | `get_pipeline` L71 | `WHERE id=%s AND tenant_id=%s` |

**注意**：`asset_manager/routes.py` 所有端点需要 `request: Request` 参数（当前未注入）以访问 `request.state.tenant_id`。同时 `verify_internal_request` 在 Step 46a 修复后会正确设置 `tenant_id`。

**验证**：`pytest tests/test_product_analyzer.py tests/test_publish_dispatcher.py tests/test_asset_manager.py`（若存在）；新增跨租户隔离测试在 Step 52。

### Step 49: 移除硬编码密码 + 创建 .env.example（P1，30 分钟）

**变更**：
1. 创建 `.env.example`——基于 `.env` 复制，但所有密码替换为占位符 `<change-me-in-production>`，并在文件头加注释说明生产环境必须通过 secret manager（Vault）注入。
2. `crawl_scheduler/routes.py` L307：`"redis://:dev_redis_2024@localhost:6379/0"` 默认值改为从 `config_manager.get("CELERY_RESULT_BACKEND", ...)` 读取，或用 `os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")`（无密码默认，开发环境靠 .env 注入）。
3. **不改动** `database/init.sql` 和 `docker-compose.yml`（这些是开发环境基础设施配置，硬编码开发密码是合理的；生产用环境变量覆盖）。

**验证**：确认 `.env.example` 存在且不含真实密码；`grep -r "dev_redis_2024" project/backend/crawl_scheduler/` 应无结果。

### Step 50: 移除 crawl_scheduler/routes.py 中的 DDL（P1，1 小时）

**变更**：
1. 更新 `database/init.sql` 的 `crawl_plans` 表定义——将 `BIGINT AUTO_INCREMENT PRIMARY KEY` 改为 `VARCHAR(64) PRIMARY KEY`（与 routes.py 使用 `uuid.uuid4()` 字符串一致），保留其他列。
2. 从 `crawl_scheduler/routes.py` 删除 L158-174 和 L198-214 的 `CREATE TABLE IF NOT EXISTS` 调用。
3. 确认 `init.sql` 的 `crawl_plans` 列与 `CrawlPlanCreate` model 字段对齐：`id, tenant_id, name, platform, keyword, category, max_count, sort_by, cron_expression, enabled, created_at, updated_at`。

**验证**：`pytest tests/test_crawl_scheduler.py`（确认 routes 不依赖 DDL 执行）。

### Step 51: 接线 RateLimiter 中间件（P2，1.5 小时）

**变更**：
1. 新建 `utils/common_sdk/middleware/__init__.py` 和 `utils/common_sdk/middleware/rate_limit.py`：
   - 实现 `RateLimitMiddleware`——基于 `RateLimiter` 实例，按路径前缀匹配，超过速率返回 429。
   - 配置：`RATE_LIMIT_PATHS = {"/api/v1/pipelines": (5, 10), "/api/v1/compose": (5, 10), "/api/v1/publish": (5, 10), "/api/v1/crawl/jobs": (5, 10)}`（rate, burst）。
   - 每个 path 独立 `RateLimiter` 实例。
2. 在各服务的 `main.py`（FastAPI app 创建处）注册中间件——`app.add_middleware(RateLimitMiddleware)`。
3. **不删除** `RateLimiter` 类本身——它仍是通用原语，中间件只是其消费者。

**验证**：`pytest tests/test_phase9_rate_limit.py`（新增——验证 429 响应）。

### Step 52: Phase 9 Part 1 测试 + 全量回归（P0，2 小时）

**新增测试文件**：

1. `tests/test_phase9_auth_unification.py`：
   - `test_verify_internal_jwt_sets_tenant_id`——valid JWT → `request.state.tenant_id` 正确
   - `test_verify_internal_jwt_rejects_invalid_token`——篡改 token → 401
   - `test_verify_internal_jwt_rejects_missing_header`——无 Authorization → 401
   - `test_mcp_verify_api_key_rejects_invalid_format`——非 `mcp_sk.` 前缀 → None
   - `test_mcp_verify_api_key_validates_against_db_hash`——mock MySQL 返回 hash，正确 key 通过，错误 key 失败
   - `test_asset_manager_verify_internal_request_sets_tenant_id`——确认不再只设 service_name

2. `tests/test_phase9_cross_tenant_isolation.py`：
   - `test_get_product_score_rejects_other_tenant`——tenant A 的产品，tenant B 的 JWT → 404
   - `test_get_pipeline_rejects_other_tenant`——同上
   - `test_delete_platform_config_rejects_other_tenant`——tenant B 无法删除 tenant A 的 config
   - `test_list_authorized_platforms_ignores_query_param_tenant_id`——伪造 query 参数无效，用 JWT 的 tenant_id
   - `test_compose_status_rejects_other_tenant_task`——tenant B 读 tenant A 的 task_id → 404

3. `tests/test_phase9_rate_limit.py`：
   - `test_rate_limit_returns_429_after_burst`——连续 11 次请求第 11 次 → 429
   - `test_rate_limit_independent_per_path`——`/pipelines` 限流不影响 `/compose`

**全量回归**：`pytest tests/ -v`——预期 156 + 新增 ~10 = 166+ passed。重点观察：
- `test_pipeline_orchestrator.py`——因 `tenant_id` 从 body 移到 JWT，需更新 mock
- `test_video_composer.py`——加 auth 后需注入 mock JWT
- `test_mcp_gateway.py`——API key 验证升级后需 mock MySQL

**文档更新**：
- `doc/security.md`（新建）——记录 auth 架构、JWT 流转、API key 验证流程、跨租户隔离原则
- `README.md`——L16 测试数更新；安全章节追加
- `.trae/documents/prodvideo_phase8_resilience_patterns_plan.md` 进度表——追加 Phase 9 Part 1 完成标记

---

## 四、Assumptions & Decisions

1. **假设**：`api_keys` 表在生产已部署且有数据（init.sql L143-157 已定义 schema）。开发环境可能为空——此时 MCP API key 验证会拒绝所有请求。**决策**：开发环境通过 `MCP_AUTH_DISABLED=true` 环境变量旁路（仅 dev），生产强制开启。
2. **假设**：`crawl_plans` 表当前数据量小（开发环境），schema 变更（BIGINT→VARCHAR）可接受。**决策**：init.sql 直接改，不做 migration 脚本（Phase 9 范围外）。
3. **决策**：不重构 `web_backend/auth.py` 的 `verify_admin_request`——它是面向外部浏览器的独立 auth 体系（cookie/session），与内部服务间 JWT 不是同一层。
4. **决策**：`GET /business_metrics` 保留无鉴权——Prometheus 抓取不应携带凭证。但添加注释说明此豁免。
5. **决策**：RateLimiter 本轮只接 4 个写入端点，不全局应用——避免读取端点被误限流影响功能。

---

## 五、验证清单

- [ ] Step 45: `grep -r "dev-jwt-secret\"" utils/ project/` 应无结果（全部统一为 `dev-jwt-secret-prodvideofactory-2024`）
- [ ] Step 46: `grep -r "verify_internal_request" project/backend/asset_manager/` 确认委托到 `verify_internal_jwt`
- [ ] Step 47: 8 个端点均有 `Depends(verify_internal_jwt)` 或 `Depends(verify_mcp_api_key)`
- [ ] Step 48: 所有按 id 查询单条记录的 SQL 均含 `AND tenant_id=%s`
- [ ] Step 49: `.env.example` 存在；`grep -r "dev_redis_2024" project/backend/crawl_scheduler/routes.py` 无结果
- [ ] Step 50: `grep "CREATE TABLE" project/backend/crawl_scheduler/routes.py` 无结果
- [ ] Step 51: `RateLimitMiddleware` 在至少 4 个服务的 main.py 注册
- [ ] Step 52: `pytest tests/` 全绿，166+ passed
- [ ] 文档：`doc/security.md` 创建；README 更新

---

## 六、进度跟踪表

| Step | 描述 | 状态 | 文件数 | 测试增量 |
|------|------|------|--------|---------|
| 45 | 统一 JWT Secret | ✅ | 2 | 0 |
| 46 | 统一 Auth 实现 | ✅ | 5+ | 0 |
| 47 | 8 端点添加鉴权 | ✅ | 3 | 0 |
| 48 | 跨租户查询修复 | ✅ | 4 | 0 |
| 49 | 硬编码密码 + .env.example | ✅ | 2 | 0 |
| 50 | 移除路由内 DDL | ✅ | 2 | 0 |
| 51 | RateLimiter 中间件 | ✅ | 5+ | 0 |
| 52 | 测试 + 回归 + 文档 | ✅ | 3 | +23 |

**总计**：~10 小时；测试 156 → 179 passed。
