# Phase 9 Part 3: API 文档 + 生产就绪计划

> **承接**：Phase 9 Part 2（E2E 测试）已完成，181 测试通过。
>
> **目标**：完善 OpenAPI 文档、生产环境 checklist、告警规则配置，更新 development_plan.md 进度。

---

## 一、当前状态

### 1.1 FastAPI 服务 OpenAPI 状态

| 服务 | title | version | description | 状态 |
|------|-------|---------|-------------|------|
| ai-generation | ✅ | ✅ | ❌ 缺失 | 需补充 |
| crawl_scheduler | ✅ | ✅ | ❌ 缺失 | 需补充 |
| product_analyzer | ✅ | ✅ | ❌ 缺失 | 补充 |
| video_composer | ✅ | ✅ | ❌ 缺失 | 需补充 |
| publish_dispatcher | ✅ | ✅ | ❌ 缺失 | 需补充 |
| asset_manager | ✅ | ✅ | ❌ 缺失 | 需补充 |
| web_backend | ✅ | ✅ | ❌ 缺失 | 需补充 |
| pipeline_orchestrator | ✅ | ❌ 缺 version | ❌ 缺失 | 需补充 |
| mcp_gateway | ✅ | ✅ | ❌ 缺失 | 需补充 |

**MCP 工具描述**（tool_registry.py）：已有详细 description，可直接用于 OpenAPI。

### 1.2 现有文档

| 文档 | 状态 | 需补充 |
|------|------|--------|
| deployment_guide.md | ✅ 基础完整 | 生产环境扩缩容、备份恢复 |
| security.md | ✅ Phase 9 Part 1 完成 | — |
| observability.md | ✅ Phase 7 完成 | 告警规则配置 |
| development_plan.md | ❌ 进度停留在早期 | 需同步到 Phase 9 |

---

## 二、实施步骤

### Step 59: 完善 FastAPI OpenAPI 元数据

为 9 个服务的 `FastAPI()` 构造函数添加：
- `description`：服务职责说明
- `version`：统一为 `"1.0.0"`（pipeline_orchestrator 补上）
- `contact`：`{"name": "ProdVideo Team"}`
- `license_info`：可选

同时为关键端点添加：
- `summary` 参数（routes.py 的 `@router.get/post`）
- `response_model` 已有，确保 `responses` 字典包含错误码说明

### Step 60: 创建生产环境 checklist

新建 `doc/production_checklist.md`，包含：
- 环境变量强制配置表（MCP_AUTH_DISABLED=false 等）
- 密钥轮换流程
- 基础设施健康验证命令
- 首次部署步骤
- 监控告警验证

### Step 61: 补充部署运维手册

在 `doc/deployment_guide.md` 追加：
- 生产环境扩缩容策略（Celery worker concurrency、GPU 资源）
- MySQL/MinIO 备份恢复流程
- 告警规则配置（Grafana + Prometheus）

### Step 62: 更新 development_plan.md 进度

将阶段 0-4 的 checkbox 标记为已完成：
- 阶段 0：全部 ✅（K8s 标记为可选）
- 阶段 1：核心服务深度开发 ✅
- 阶段 2：MCP Gateway ✅
- 阶段 3：前端 ✅
- 阶段 4：集成测试 ✅

---

## 三、进度跟踪表

| Step | 描述 | 状态 | 文件数 |
|------|------|------|--------|
| 59 | OpenAPI 元数据完善 | ✅ | 9 |
| 60 | 生产环境 checklist | ✅ | 1 |
| 61 | 部署运维手册补充 | ✅ | 1 |
| 62 | development_plan.md 更新 | ✅ | 1 |

**总计**：完成。181 测试通过。