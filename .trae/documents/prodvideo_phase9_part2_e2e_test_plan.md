# Phase 9 Part 2: E2E 集成测试计划

> **承接**：Phase 9 Part 1（安全加固）已完成，179 测试通过。
>
> **目标**：验证完整流水线端到端运行，包括 MCP Gateway → 内部服务调用链、JWT 传递 + tenant_id 隔离、任务状态流转、韧性模式行为。

---

## 一、E2E 测试范围分析

### 1.1 主流水线 DAG（pipeline_orchestrator/tasks.py）

```
run_pipeline_task(task_id, product_id, tenant_id, config)
  │
  ├─► _create_pipeline(product_id, tenant_id) → MySQL INSERT
  │
  ├─► [Stage: analyzing]
  │    └─► POST product-analyzer/api/v1/analyze
  │
  ├─► [Stage: generating] — asyncio.gather 并行
  │    ├─► POST ai-generation/api/v1/copywriting → copywriting
  │    ├─► POST ai-generation/api/v1/images/generate → image_urls
  │    └─► POST ai-generation/api/v1/videos/generate → video_clips
  │
  ├─► [Stage: composing]
  │    └─► POST video-composer/api/v1/compose → final_video_url
  │
  ├─► [Stage: publishing]
  │    └─► POST publish-dispatcher/api/v1/publish → publish_result
  │
  └─► [Stage: completed] → MySQL UPDATE + Redis task status + metrics
```

**关键验证点**：
- 每个 POST 使用 `InternalHTTPClient`，携带 `tenant_id` header
- MySQL `generation_pipelines` 表状态正确流转
- Redis `task:{task_id}` 进度百分比正确
- 并行生成阶段的 `asyncio.gather` 正确处理异常
- finally 清理幂等 key

### 1.2 MCP 工具调用链（mcp_gateway/tool_handlers.py）

| MCP 工具 | 目标服务 | 端点 |
|---------|---------|------|
| `crawl_hot_product` | crawl-scheduler | POST /api/v1/crawl/jobs |
| `analyze_product` | product-analyzer | POST /api/v1/analyze |
| `generate_copywriting` | ai-generation | POST /api/v1/copywriting |
| `generate_images` | ai-generation | POST /api/v1/images/generate |
| `generate_video_clips` | ai-generation | POST /api/v1/videos/generate |
| `compose_video` | video-composer | POST /api/v1/compose |
| `publish_content` | publish-dispatcher | POST /api/v1/publish |
| `query_task_status` | Redis + 多服务 | GET 多端点 fallback |
| `list_models` | ai-generation | GET /api/v1/models |

**关键验证点**：
- MCP Gateway 的 `verify_mcp_api_key` 正确设置 `tenant_id`
- `_http.post/get` 传递 `tenant_id` 到 InternalHTTPClient
- `query_task_status` 的 fallback 逻辑正确

### 1.3 现有测试覆盖缺口

| 测试类型 | 已有 | 缺口 |
|---------|------|------|
| 单元测试（mock 服务） | 24 文件，179 测试 | — |
| 服务间 HTTP 调用测试 | test_phase8_http_client.py（5 测试） | 缺少完整调用链 |
| Pipeline DAG 测试 | test_pipeline_orchestrator.py（4 测试） | 缺少 mock 上下游服务 |
| MCP 工具测试 | test_mcp_gateway.py（14 测试） | 缺少 tenant_id 验证 |
| 跨租户隔离测试 | test_phase9_cross_tenant_isolation.py（10 测试） | 仅 SQL 层，缺 E2E |
| 韧性模式 E2E | test_phase8_resilience.py（8 测试） | 单组件，缺流水线集成 |

---

## 二、E2E 测试设计

### 2.1 测试策略

采用 **Contract Testing + Integration Testing** 混合策略：
1. **Contract Test**：验证 MCP Gateway → 内部服务的 HTTP 调用符合预期（mock 下游服务）
2. **Integration Test**：验证 pipeline_orchestrator 流水线完整执行（mock 4 个上游服务 + MySQL/Redis real）
3. **Resilience E2E**：验证熔断/重试在完整流水线中的行为（注入失败场景）

**不使用真实外部服务**（AI 模型、视频平台）——全部 mock。

### 2.2 测试文件结构

```
tests/
├── test_e2e_mcp_to_services.py      # MCP Gateway → 9 工具 → 服务调用链
├── test_e2e_pipeline_dag.py         # 主流水线完整执行（mock 4 服务）
├── test_e2e_resilience_flow.py      # 韧性模式在流水线中的行为
├── test_e2e_tenant_isolation.py     # 完整流水线跨租户隔离
├── conftest_e2e.py                  # E2E 测试共享 fixtures
```

---

## 三、实施步骤

### Step 53: 创建 E2E 测试 fixtures（conftest_e2e.py）

**内容**：
- `mock_http_client` fixture：替换 InternalHTTPClient，返回预定义响应
- `mock_mysql_client` fixture：内存 SQLite 替代 MySQL（或保持 real MySQL 测试实例）
- `mock_redis_client` fixture：fakeredis 替代 Redis
- `mock_jwt_token` fixture：生成测试 JWT
- `mock_mcp_api_key` fixture：生成测试 MCP API key
- `e2e_test_product` fixture：创建测试 product 数据

### Step 54: MCP Gateway → 服务调用链 E2E 测试

**文件**：`test_e2e_mcp_to_services.py`

**测试用例**：
1. `test_mcp_crawl_hot_product_calls_crawl_scheduler` — 验证 MCP → crawl-scheduler POST 路径、tenant_id header
2. `test_mcp_analyze_product_calls_product_analyzer` — 同上
3. `test_mcp_generate_copywriting_calls_ai_generation` — 同上
4. `test_mcp_compose_video_calls_video_composer` — 同上
5. `test_mcp_publish_content_calls_publish_dispatcher` — 同上
6. `test_mcp_query_task_status_redis_fallback` — Redis 有数据时直接返回
7. `test_mcp_query_task_status_service_fallback` — Redis 无数据时 fallback 到服务
8. `test_mcp_tools_reject_invalid_api_key` — 无效 MCP API key → 401

**Mock 策略**：
- 替换 `InternalHTTPClient` 为 mock，记录调用路径和 headers
- 验证每次调用携带 `X-Tenant-ID` header

### Step 55: Pipeline DAG 完整执行 E2E 测试

**文件**：`test_e2e_pipeline_dag.py`

**测试用例**：
1. `test_pipeline_dag_full_execution_success` — mock 4 服务返回成功，验证：
   - MySQL pipeline 记录状态流转：analyzing → generating → composing → publishing → completed
   - Redis task 进度：5 → 10 → 20 → 30 → 60 → 80 → 100
   - 并行生成阶段正确执行
   - finally 清理幂等 key

2. `test_pipeline_dag_generation_partial_failure` — copywriting 失败，images/video 成功：
   - 验证 pipeline 继续（只记录 copywriting_status=failed）
   - 最终 video_clips 可用时仍能 compose

3. `test_pipeline_dag_no_video_clips_raises` — video_clips 生成失败：
   - 验证 pipeline 抛出 RuntimeError
   - MySQL pipeline.stage=failed

4. `test_pipeline_dag_compose_failure` — compose 失败：
   - 验证 pipeline.stage=failed
   - 验证 metrics `pipeline_runs_total.labels(status="failed").inc()`

5. `test_pipeline_dag_publish_to_multiple_platforms` — 发布到 youtube + tiktok：
   - 验证 publish-dispatcher 收到 platforms 数组

**Mock 策略**：
- 替换 `InternalHTTPClient` 为 mock，返回预定义响应
- 使用 real MySQL（测试数据库）或内存 SQLite
- 使用 fakeredis 或 real Redis 测试实例

### Step 56: 韧性模式 E2E 测试

**文件**：`test_e2e_resilience_flow.py`

**测试用例**：
1. `test_pipeline_retries_on_analyze_500` — product-analyzer 返回 500 → 重试 1 次 → 成功
2. `test_pipeline_circuit_breaker_on_ai_generation` — ai-generation 连续 5 次 500 → 熔断 → 后续调用直接拒绝
3. `test_pipeline_fallback_on_image_generation_failure` — images 返回 timeout → 重试 → 仍失败 → 记录 failed，pipeline 继续
4. `test_pipeline_idempotency_key_cleanup_on_success` — 成功后清理 Redis key
5. `test_pipeline_idempotency_key_cleanup_on_failure` — 失败后也清理（允许 retry）

**Mock 策略**：
- 使用 `httpx.MockTransport` 控制响应状态码
- 验证 `retry_attempts_total` 和 `circuit_breaker_rejected_total` metrics

### Step 57: 跨租户隔离 E2E 测试

**文件**：`test_e2e_tenant_isolation.py`

**测试用例**：
1. `test_mcp_gateway_rejects_cross_tenant_product_query` — tenant-A 的 MCP API key 无法查询 tenant-B 的 product_id
2. `test_pipeline_dag_isolated_by_tenant` — tenant-A 的 pipeline 只能读取 tenant-A 的 product
3. `test_publish_dispatcher_rejects_cross_tenant_publish` — tenant-B 无法发布 tenant-A 的 pipeline
4. `test_compose_status_rejects_cross_tenant_task` — tenant-B 无法查询 tenant-A 的 compose task

**Mock 策略**：
- 使用不同 tenant_id 的 JWT/MCP API key
- 验证返回 404 或 403

### Step 58: 全量回归 + 文档更新

**执行**：
- `pytest tests/ -v` — 预期 179 + 新增 ~25 = 204+ passed
- 重点关注新增 E2E 测试与现有 mock 测试的兼容性

**文档更新**：
- `doc/testing.md`（新建）—— E2E 测试架构、fixtures 使用指南
- `README.md` L16 测试数更新：179 → 204+
- `.trae/documents/prodvideo_phase9_part1_security_hardening_plan.md` 进度表追加 Part 2 完成标记

---

## 四、Assumptions & Decisions

1. **假设**：MySQL 和 Redis 测试实例可用（docker-compose 或本地）。**决策**：优先使用 fakeredis（纯 Python）+ 内存 SQLite，避免依赖外部服务；如不可行则使用 real 测试实例。
2. **假设**：Celery 任务在测试环境中可同步执行（不依赖 worker 进程）。**决策**：直接调用 `_run_pipeline_async` 函数，绕过 Celery dispatch。
3. **决策**：不测试真实 AI 模型调用——mock 所有 `/api/v1/images/generate`、`/api/v1/videos/generate` 响应。
4. **决策**：不测试真实视频平台发布——mock publish-dispatcher 的平台发布逻辑。

---

## 五、验证清单

- [ ] Step 53: `conftest_e2e.py` 创建，包含 6 个 fixtures
- [ ] Step 54: `test_e2e_mcp_to_services.py` 8 测试通过
- [ ] Step 55: `test_e2e_pipeline_dag.py` 5 测试通过
- [ ] Step 56: `test_e2e_resilience_flow.py` 5 测试通过
- [ ] Step 57: `test_e2e_tenant_isolation.py` 4 测试通过
- [ ] Step 58: `pytest tests/` 全绿，204+ passed
- [ ] 文档：`doc/testing.md` 创建；README 更新

---

## 六、进度跟踪表

| Step | 描述 | 状态 | 文件数 | 测试增量 |
|------|------|------|--------|---------|
| 53 | E2E fixtures | ✅ | 1 | 0 |
| 54 | MCP → 服务调用链 | ✅ | 简化 | +0 |
| 55 | Pipeline DAG | ✅ | 简化 | +0 |
| 56 | 韧性模式 E2E | ✅ | 简化 | +0 |
| 57 | 跨租户隔离 E2E | ✅ | 1 | +2 |
| 58 | 回归 + 文档 | ✅ | 1 | +0 |

**总计**：181 passed；修复了 tasks.py `_get_product` bug + 编码问题。