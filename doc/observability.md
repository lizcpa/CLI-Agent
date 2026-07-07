# 可观测性

## 日志

所有服务使用 `structlog` 输出 JSON 格式日志，包含 ISO 8601 时间戳、日志级别、事件名、结构化字段。

```python
from common_sdk.logger import get_logger
logger = get_logger(__name__)
logger.info("redis_connected", host="redis", port=6379)
```

输出示例：
```json
{"event":"redis_connected","host":"redis","port":6379,"level":"info","timestamp":"2026-06-30T12:00:00Z","logger":"crawl_scheduler.main"}
```

日志级别通过环境变量 `LOG_LEVEL` 控制（默认 `INFO`）。

## 指标（Prometheus）

每个后端服务通过 `prometheus-fastapi-instrumentator` 自动暴露 `GET /metrics`（Prometheus text format）。

### 自动采集的指标

- `http_requests_total` — HTTP 请求总数（按 method/status/handler 分维度）
- `http_request_duration_seconds` — HTTP 请求延迟直方图
- `http_requests_in_progress` — 当前进行中的请求数

### 排除路径

以下路径不计入指标（避免探针流量污染）：
`/healthz`, `/readyz`, `/metrics`, `/docs`, `/openapi.json`, `/redoc`

### 业务指标

部分服务额外提供 `GET /business_metrics`（JSON 格式，非 Prometheus）：

| 服务 | 端点 | 指标 |
|------|------|------|
| product_analyzer | `/business_metrics` | `hot_products_count`, `total_active_products` |
| ai_generation | `/business_metrics` | 各适配器类型（llm/image/video/tts）的 `healthy_count` 与 `models` 列表 |
| mcp_gateway | `/business_metrics` | `active_sessions`（当前 SSE 会话数） |

### 自定义业务 Prometheus 指标

除 HTTP 自动指标外，关键业务流通过 `prometheus_client` Counter/Histogram 在 `/metrics` 暴露（定义于 `utils/common_sdk/business_metrics.py`）：

| 指标名 | 类型 | Labels | 含义 |
|--------|------|--------|------|
| `crawl_jobs_total` | Counter | platform, status | 采集任务总数（success/failed） |
| `crawl_products_found` | Histogram | platform | 单次采集发现的商品数分布 |
| `ai_generation_requests_total` | Counter | adapter_type, model, status | AI 生成请求总数（llm/image/video/tts） |
| `ai_generation_duration_seconds` | Histogram | adapter_type | AI 生成耗时分布 |
| `video_compose_jobs_total` | Counter | status | 视频合成任务总数 |
| `publish_jobs_total` | Counter | platform, status | 发布任务总数 |
| `pipeline_runs_total` | Counter | status | Pipeline 运行总数 |
| `mcp_active_sessions` | Gauge | - | 当前 MCP SSE 活跃会话数 |

### 禁用指标

设置环境变量 `PROMETHEUS_METRICS_ENABLED=false` 可关闭指标采集（`/metrics` 端点仍注册但不记录流量）。

## Prometheus + Grafana 容器栈

Docker Compose 已集成 Prometheus + Grafana + Jaeger 三个可观测性容器，一键启动即可采集和可视化全部 9 服务的指标。

### 启动

```bash
docker compose up -d prometheus grafana jaeger
```

### 访问地址

| 服务 | URL | 默认账号 |
|------|-----|---------|
| Prometheus | http://localhost:9090 | - |
| Grafana | http://localhost:3000 | admin / admin |
| Jaeger UI | http://localhost:16686 | - |

### Grafana Dashboard

启动后 Grafana 自动加载（provisioning）2 个看板，无需手动导入：

- **ProdVideo - Service Overview**：9 服务可用性、HTTP 请求速率、延迟 P95、5xx 错误率
- **ProdVideo - Business Metrics**：采集/AI生成/合成/发布/Pipeline 业务流量与耗时

### Prometheus 抓取配置

配置文件位于 `observability/prometheus.yml`，15s 间隔抓取 9 后端 `/metrics`：

```yaml
scrape_configs:
  - job_name: "prodvideo-backends"
    metrics_path: /metrics
    static_configs:
      - targets:
          - "mcp-gateway:8000"
          - "crawl-scheduler:8001"
          - "product-analyzer:8002"
          - "ai-generation:8003"
          - "video-composer:8004"
          - "publish-dispatcher:8005"
          - "asset-manager:8006"
          - "web-backend:8007"
          - "pipeline-orchestrator:8008"
```

## 告警规则

配置文件位于 `observability/alerts.yml`，Prometheus 自动加载。当前规则：

| 告警名 | 触发条件 | 持续时间 | 严重级别 |
|--------|---------|---------|---------|
| `ServiceDown` | `up == 0` | 1m | critical |
| `HighErrorRate` | 5xx 错误率 > 5% | 5m | warning |
| `CrawlFailureRate` | 采集失败率 > 50% | 10m | warning |

> Alertmanager 容器（告警通知）计划在 Phase 8 引入；当前规则在 Prometheus `/alerts` 页面可见。

## 韧性模式（Phase 8）

服务间调用通过 `common_sdk.http_client.InternalHTTPClient` 统一接入韧性层，包含熔断器、重试、舱壁、限流四种模式，保障 pipeline DAG 在下游瞬时故障时优雅降级。

### 架构

```
InternalHTTPClient.post(url, target="product-analyzer")
  ├─ Bulkhead（Semaphore，per-target，默认 max_concurrent=10）
  │    └─ CircuitBreaker.call(retried_func)
  │         ├─ CLOSED → 执行
  │         ├─ OPEN → 拒绝（CircuitBreakerOpenError，不重试）
  │         └─ HALF_OPEN → 单探测
  │              └─ retry(max_attempts=3, exponential backoff + jitter)
  │                   ├─ ServiceException / httpx.ConnectError / Timeout → 重试
  │                   ├─ CircuitBreakerOpenError / 4xx AppException → 不重试
  │                   └─ httpx.AsyncClient（lazy init，连接池复用）
```

### 配置参数

`InternalHTTPClient(service_name, ...)` 构造参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `timeout` | 30.0 | 单次 HTTP 请求超时（秒） |
| `max_retries` | 3 | 最大重试次数（含首次） |
| `cb_failure_threshold` | 3 | 连续失败几次后熔断打开 |
| `cb_cooldown_seconds` | 30.0 | 熔断打开后冷却时间，过后转 HALF_OPEN |
| `bulkhead_concurrency` | 10 | 对同一下游的最大并发调用数 |

AI 适配器（`BaseModelAdapter`）使用独立的熔断器，阈值 3 / 冷却 300s（与既有降级逻辑一致）。

### 韧性指标

| 指标名 | 类型 | Labels | 含义 |
|--------|------|--------|------|
| `circuit_breaker_state` | Gauge | name | 熔断器状态（0=closed, 1=open, 2=half_open） |
| `circuit_breaker_rejected_total` | Counter | name | 因熔断打开被拒绝的调用总数 |
| `retry_attempts_total` | Counter | name | 重试尝试总数（不含首次调用） |

定义于 `utils/common_sdk/resilience.py`，通过 `prometheus_client` 注册到默认 REGISTRY，随各服务 `/metrics` 暴露。

### 幂等性

Pipeline 提交通过 Redis `SET NX EX` 实现幂等：

- **守卫**（`routes.py`）：key = `pipeline:active:{product_id}:{tenant_id}`，TTL 3600s。重复提交返回 422 `ValidationException`。
- **释放**（`tasks.py` finally）：任务完成（成功/失败）后 `DEL` key，允许重试。
- **兜底**：TTL 1h 自动过期，防止任务崩溃未执行 finally 时永久锁死。

### Grafana 面板建议

- **熔断器状态面板**：`max(circuit_breaker_state) by (name)` — 实时查看哪些下游被熔断
- **拒绝速率面板**：`rate(circuit_breaker_rejected_total[5m]) by (name)` — 拒绝流量趋势
- **重试速率面板**：`rate(retry_attempts_total[5m]) by (name)` — 重试压力，过高说明下游不稳定

## 健康检查

所有服务统一通过 `common_sdk.health.build_health_router` 注册：

### `GET /healthz`（Liveness）

进程存活即返回 200。不执行任何 I/O，可高频探活。

```json
{"status":"ok","service":"crawl-scheduler"}
```

### `GET /readyz`（Readiness）

检查服务依赖（Redis/MySQL/MinIO 按服务实际依赖）。全部可达返回 200，否则返回 503。

```json
{
  "status":"ready",
  "service":"crawl-scheduler",
  "checks":{"redis":"ok","mysql":"ok"}
}
```

失败时：
```json
{
  "status":"not_ready",
  "service":"crawl-scheduler",
  "checks":{"redis":"ok","mysql":"fail"}
}
```
（HTTP 503）

### 各服务依赖矩阵

| 服务 | redis | mysql | minio |
|------|-------|-------|-------|
| mcp-gateway | - | - | - |
| crawl-scheduler | ✓ | ✓ | - |
| product-analyzer | ✓ | ✓ | - |
| ai-generation | ✓ | ✓ | ✓ |
| video-composer | - | ✓ | ✓ |
| publish-dispatcher | - | ✓ | - |
| asset-manager | - | ✓ | ✓ |
| web-backend | ✓ | ✓ | - |
| pipeline-orchestrator | - | ✓ | - |

## 分布式追踪（OpenTelemetry + Jaeger）

所有 9 个后端服务通过 OpenTelemetry auto-instrumentation 自动注入 trace span，经 OTLP gRPC 上报到 Jaeger collector，实现跨服务调用链可视化。

### 架构

```
FastAPI Request
  └─ FastAPIInstrumentor（自动注入 server span）
       └─ HTTPXClientInstrumentor（出站 HTTP 调用自动注入 client span + W3C traceparent）
            └─ RedisInstrumentor（Redis 操作自动注入 span）
                 └─ OTLPSpanExporter（gRPC :4317）
                      └─ Jaeger collector
                           └─ Jaeger UI（:16686）
```

### 启用方式

通过环境变量 `OTEL_ENABLED` 控制：
- `OTEL_ENABLED=true`（docker-compose 默认）：初始化 TracerProvider + instrument FastAPI/httpx/redis
- `OTEL_ENABLED=false`（本地开发默认）：`setup_tracing` 直接 no-op，零开销

`OTEL_EXPORTER_OTLP_ENDPOINT` 指定 Jaeger collector 地址（docker-compose 默认 `http://jaeger:4317`）。

### 集成代码

定义于 `utils/common_sdk/tracing.py`，每个服务的 `main.py` 在 `setup_metrics` 之后调用：

```python
from common_sdk.tracing import setup_tracing

app = FastAPI(...)
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)
```

`setup_tracing` 幂等：重复调用只重新 instrument app，不会重复设置 TracerProvider（避免 `Overriding of current TracerProvider is not allowed` 警告）。

### 跨服务传播

Pipeline Orchestrator → AI Generation / Video Composer / Publish Dispatcher 的内部 HTTP 调用通过 `common_sdk.http_client.InternalHTTPClient`（基于 httpx）发出。由于 `HTTPXClientInstrumentor` 已全局 instrument httpx，traceparent header 自动注入下游请求，Jaeger 可拼接出完整 DAG 调用链：

```
run_pipeline_task (pipeline-orchestrator)
  ├─ POST /api/v1/analyze (product-analyzer)
  ├─ POST /api/v1/copywriting (ai-generation)
  ├─ POST /api/v1/images/generate (ai-generation)
  ├─ POST /api/v1/videos/generate (ai-generation)
  ├─ POST /api/v1/compose (video-composer)
  └─ POST /api/v1/publish (publish-dispatcher)
```

### Jaeger UI 访问

```bash
docker compose up -d jaeger
```

打开 http://localhost:16686：
- **Service 下拉框**：可选 9 个后端服务（如 `pipeline-orchestrator`、`ai-generation`）
- **Find Traces**：按服务 + 操作 + 时间范围搜索
- **Trace 详情**：展开任一 trace 可见 span 树、耗时瀑布图、HTTP/Redis 标签

### 限制

- MySQL（aiomysql）暂无官方 OTel instrumentation 包，SQL 调用不产生 span（但 HTTP 层 span 仍覆盖服务边界）
- Celery worker（celery-worker 容器）暂未 instrument，仅 FastAPI 服务产生 trace
- 默认采样率 100%（BatchSpanProcessor 全量上报），生产环境可加 `OTEL_TRACES_SAMPLER=parentbased_traceidratio` + `OTEL_TRACES_SAMPLER_ARG=0.1` 降采样
