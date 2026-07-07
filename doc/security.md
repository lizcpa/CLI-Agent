# 安全架构

## 概述

ProdVideo AI Factory 采用双层认证体系：

1. **内部服务间认证**（Service-to-Service）：JWT（HS256），由 Kong API 网关签发
2. **外部客户端认证**（Client-to-MCP）：API Key（SHA-256 哈希存储），通过 MCP Gateway 验证

所有数据查询均按 `tenant_id` 隔离，确保多租户数据安全。

---

## 一、内部服务间 JWT 认证

### 1.1 架构

```
Kong (JWT Issuer)
  ↓签发 HS256 JWT
InternalHTTPClient (携带 Bearer token)
  ↓
verify_internal_jwt (FastAPI Dependency)
  ↓设置 request.state.tenant_id + request.state.service_name
路由处理函数
```

### 1.2 实现

**Canonical 实现**：`utils/common_sdk/auth.py`

- `create_service_jwt(service_name, secret)` — 签发 JWT（1 小时有效期）
- `verify_internal_jwt(request, authorization, x_tenant_id)` — FastAPI 依赖，验证 JWT 并注入 `request.state.tenant_id` 和 `request.state.service_name`

**JWT Secret 配置**：
- 环境变量 `INTERNAL_JWT_SECRET`
- 默认值（仅开发环境）：`dev-jwt-secret-prodvideofactory-2024`
- 所有服务统一使用同一 secret（`common_sdk/auth.py`、`http_client.py`、`ai_generation/config.py`）

### 1.3 服务 Auth 模块

| 服务 | Auth 模块 | 说明 |
|------|----------|------|
| product_analyzer | `from common_sdk.auth import verify_internal_jwt` | 别名 re-export |
| crawl_scheduler | `from common_sdk.auth import verify_internal_jwt` | 别名 re-export |
| asset_manager | `from common_sdk.auth import verify_internal_jwt as verify_internal_request` | 别名 re-export |
| ai_generation | 直接 `from common_sdk.auth import verify_internal_jwt` | 已统一 |
| publish_dispatcher | 直接 `from utils.common_sdk.auth import verify_internal_jwt as verify_internal_request` | 已统一 |
| video_composer | 直接 `from utils.common_sdk.auth import verify_internal_jwt` | 已统一 |
| pipeline_orchestrator | 直接 `from common_sdk.auth import verify_internal_jwt` | 已统一 |
| mcp_gateway | 使用独立 API Key 认证（见下文） | — |

### 1.4 豁免端点

以下端点不要求 JWT 认证：
- `/healthz`、`/readyz` — 健康检查
- `/metrics` — Prometheus 指标抓取
- `/business_metrics` — MCP Gateway 业务指标
- `/docs`、`/openapi.json`、`/redoc` — API 文档

---

## 二、MCP Gateway API Key 认证

### 2.1 架构

```
Client (携带 Authorization: Bearer mcp_sk.<tenant>.<secret>)
  ↓
verify_mcp_api_key (FastAPI Dependency)
  ↓解析 tenant_id + SHA-256 哈希比对
api_keys 表 (MySQL)
  ↓设置 request.state.tenant_id + request.state.scopes
MCP 路由处理函数
```

### 2.2 API Key 格式

```
mcp_sk.<tenant_id>.<64-char-hex-secret>
```

- 前缀：`mcp_sk.`
- 第二段：租户 ID
- 第三段：64 字符十六进制随机密钥

### 2.3 存储

API Key 以 SHA-256 哈希存储在 `api_keys` 表中：

| 列 | 说明 |
|----|------|
| `tenant_id` | 租户 ID |
| `api_key_hash` | SHA-256 哈希 |
| `scopes` | 权限范围（JSON） |
| `enabled` | 是否启用 |
| `expires_at` | 过期时间（NULL = 永不过期） |

### 2.4 开发旁路

开发环境可通过 `MCP_AUTH_DISABLED=true` 跳过 API Key 验证（设置 `tenant_id=default`）。**生产环境必须设为 `false`**。

### 2.5 Key 生成

使用 `common_sdk.auth.create_api_key(tenant_id, scopes)` 生成新 key，返回 `(raw_key, key_hash)`。将 `key_hash` 存入数据库，将 `raw_key` 返回给客户端（仅此一次）。

---

## 三、多租户数据隔离

### 3.1 原则

所有数据库表均包含 `tenant_id VARCHAR(64) NOT NULL DEFAULT 'default'` 列。所有按主键查询、更新、删除的 SQL 必须包含 `AND tenant_id = %s` 条件，`tenant_id` 从 `request.state.tenant_id` 获取（由认证中间件注入）。

### 3.2 已实施隔离的查询

| 服务 | 端点 | 隔离方式 |
|------|------|---------|
| product_analyzer | `GET /products/{id}/score` | `WHERE id=%s AND tenant_id=%s` |
| product_analyzer | `GET /products/hot` | `WHERE id IN (...) AND tenant_id=%s` |
| publish_dispatcher | `GET /publish/{task_id}` | `WHERE platform_post_id=%s AND tenant_id=%s` |
| publish_dispatcher | `GET /publish/pipeline/{pipeline_id}` | `WHERE pipeline_id=%s AND tenant_id=%s` |
| publish_dispatcher | `GET /platforms` | 使用 JWT 的 tenant_id，不接受 Query 参数 |
| asset_manager | `GET /platform-configs` | `WHERE tenant_id=%s` |
| asset_manager | `POST /platform-configs` | INSERT 含 tenant_id；UPDATE 含 `AND tenant_id=%s` |
| asset_manager | `DELETE /platform-configs/{id}` | `WHERE id=%s AND tenant_id=%s` |
| pipeline_orchestrator | `GET /pipelines/{id}` | `WHERE id=%s AND tenant_id=%s` |
| pipeline_orchestrator | `POST /pipelines` | tenant_id 从 JWT 获取，不接受 body 参数 |
| video_composer | `GET /compose/{task_id}` | 检查 Redis 中 task 的 tenant_id 匹配 |
| video_composer | `GET /compose` | 仅列出当前 tenant 的 tasks |

---

## 四、速率限制

### 4.1 实现

`utils/common_sdk/middleware/rate_limit.py` — 基于 Token Bucket 算法的 ASGI 中间件。

### 4.2 配置

| 路径前缀 | 速率 (tokens/sec) | 突发 (burst) |
|---------|-------------------|-------------|
| `/api/v1/pipelines` | 5 | 10 |
| `/api/v1/compose` | 5 | 10 |
| `/api/v1/publish` | 5 | 10 |
| `/api/v1/crawl/jobs` | 5 | 10 |

超过速率的请求返回 HTTP 429。

### 4.3 注册

以下服务已注册 `RateLimitMiddleware`：
- pipeline_orchestrator
- video_composer
- publish_dispatcher
- crawl_scheduler

---

## 五、密钥管理

### 5.1 开发环境

- 使用 `.env` 文件，包含开发环境默认密码
- `.env.example` 提供模板，所有密码以 `<change-me-in-production>` 占位

### 5.2 生产环境

- 所有密钥通过 Vault secret manager 注入
- `INTERNAL_JWT_SECRET`：至少 32 字符随机字符串
- `MYSQL_PASSWORD`、`REDIS_PASSWORD`、`MINIO_ROOT_PASSWORD` 等：强密码
- `MCP_AUTH_DISABLED`：必须设为 `false`

### 5.3 禁止硬编码密码

代码中不应硬编码任何密码。`crawl_scheduler/routes.py` 的 `_parse_redis_url()` 使用 `os.getenv("CELERY_RESULT_BACKEND")` 读取连接字符串，不再硬编码 Redis 密码。
