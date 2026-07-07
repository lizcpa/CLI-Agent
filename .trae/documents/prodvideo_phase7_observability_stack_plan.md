# Phase 7：可观测性栈闭环（Observability Stack Closure）

> 目标：让 Phase 6 暴露的 `/metrics` 端点**真正被采集和可视化**，加上分布式追踪和自定义业务指标，形成完整的可观测性闭环。同时修复 Phase 6 残留问题（2 个失败测试 + mcp_gateway 路由冲突）。
>
> 完成后平台具备：一键 `docker compose up` 起全栈 → Grafana 看板实时展示服务/业务指标 → Jaeger 追踪跨服务调用链 → 告警规则覆盖核心异常。

---

## 1. 当前状态分析（Phase 1 探索结论）

### 1.1 Phase 6 已交付（可观测性基础）
- **9 服务统一暴露** `/metrics`（prometheus-fastapi-instrumentator 自动 HTTP latency/status/rate）+ `/healthz` + `/readyz`
- **业务指标端点**：product_analyzer、ai_generation 已有 `/business_metrics`（JSON 格式，非 Prometheus）
- **structlog JSON 日志**：统一格式，ISO 时间戳
- **docker-compose.yml**：全栈 25 服务可一键启动
- **CI**：GitHub Actions 跑 pytest

### 1.2 关键 Gap（Phase 7 必须解决）
| # | Gap | 影响 |
|---|---|---|
| G1 | **无 Prometheus 采集**：9 服务 /metrics 暴露了但无 scraper，数据未被收集 | 指标端点形同虚设 |
| G2 | **无 Grafana 可视化**：无 dashboard，运维无法直观观测服务健康/业务流量 | 可观测性不可用 |
| G3 | **无分布式追踪**：9 服务间调用（httpx + Celery）无 trace context 传播，跨服务链路不可见 | 故障定位困难 |
| G4 | **零自定义业务指标**：crawl/analyze/generate/compose/publish 关键业务流无 Counter/Histogram，只有 HTTP 层指标 | 业务监控盲区 |
| G5 | **mcp_gateway 路由冲突**（Phase 6 残留）：routes.py 手写 `/healthz`/`/readyz`/`/metrics`（lines 85-97）与 main.py 的 `build_health_router` + `setup_metrics` 重复注册，手写版覆盖工厂版（`/healthz` 返回 `{"status":"ok"}` 无 service 字段） | mcp_gateway 健康检查不符合规范 |
| G6 | **Phase 6 的 2 个集成测试失败**：`test_crawl_scheduler_main_exposes_health_and_metrics`、`test_pipeline_orchestrator_main_exposes_metrics` 用 `app.routes` 检查路径，但 FastAPI `include_router` 添加的路由不反映在 `app.routes` 属性中 | CI 测试红线 |
| G7 | **无告警规则**：服务宕机/高错误率/爬虫失败率升高无自动告警 | 异常不可及时发现 |

### 1.3 显式 DEFER 到 Phase 8（不在本轮范围）
- **Kong 声明式路由配置**：9 服务路由 + JWT 插件 + 限流插件，独立大块；当前直接访问 8001-8008 端口可用。
- **Nacos 服务注册**：`.env` 默认 `NACOS_ENABLED=false`，fail-soft 已工作；服务注册非阻塞项。
- **AWS SigV4 签名**（Amazon PA-API）：依赖具体业务需求，留 Phase 8。
- **生产级 worker 拆分**：按队列独立容器 + autoscale，需 K8s 环境，留 Phase 8。
- **K8s manifest / Helm chart**：留 Phase 8+。
- **Playwright E2E 浏览器集成测试**：当前全 mock 够用，留 Phase 8。

---

## 2. 决策（D1-D10）

- **D1 — Phase 7 主题**：可观测性栈闭环 = Prometheus 采集 + Grafana 可视化 + Jaeger 追踪 + 自定义业务指标 + 告警规则。Kong/Nacos 留 Phase 8。
- **D2 — 追踪方案**：用 OpenTelemetry（OTLP exporter → Jaeger）。新建 `utils/common_sdk/tracing.py` 工厂 `setup_tracing(app, service_name)`，自动 instrument FastAPI + httpx + aiomysql + redis。9 服务 main.py 调用。`InternalHTTPClient` 注入 W3C `traceparent` header 传播上下文。
- **D3 — 业务指标库**：用 `prometheus_client`（已在 requirements.txt）的 Counter/Histogram。新建 `utils/common_sdk/business_metrics.py` 集中定义指标注册器，通过默认 registry 在 `/metrics` 自动暴露（无需额外端点）。
- **D4 — 业务指标命名规范**：`<domain>_<action>_total`（Counter）/ `<domain>_<action>_duration_seconds`（Histogram），labels 含 `service`/`platform`/`status`/`adapter_type` 等。具体：
  - `crawl_jobs_total`{platform, status}
  - `crawl_products_found`{platform} (Histogram)
  - `ai_generation_requests_total`{adapter_type, model, status}
  - `ai_generation_duration_seconds`{adapter_type}
  - `video_compose_jobs_total`{status}
  - `publish_jobs_total`{platform, status}
  - `pipeline_runs_total`{status}
- **D5 — mcp_gateway 路由冲突修复**：删除 routes.py 中手写的 `/healthz`/`/readyz`/`/metrics`（lines 85-97）；将 `active_sessions` 业务数据迁移为 `/business_metrics` 端点（与 product_analyzer/ai_generation 一致），同时用 prometheus_client Gauge `mcp_active_sessions` 暴露在 /metrics。
- **D6 — Phase 6 失败测试修复**：将 `test_crawl_scheduler_main_exposes_health_and_metrics`、`test_pipeline_orchestrator_main_exposes_metrics` 的断言从 `{r.path for r in app.routes}` 改为 TestClient 实际 GET `/healthz`/`/metrics` 验证 200。需 mock lifespan 依赖（Redis/MySQL）以避免启动失败——用 `app.router.lifespan_context = None` 或构造不带 lifespan 的 app 副本。**决策：用 monkeypatch 把 `get_mysql_client`/`get_redis_client` 的 `create_pool`/`connect`/`ping` 替换为 no-op AsyncMock**，让 TestClient 启动 app 不连真实 DB。
- **D7 — Prometheus 容器配置**：新建 `observability/prometheus.yml`，scrape 9 后端服务 `:8001-8008` + `:8010`(mcp_gateway) 的 `/metrics`，15s 间隔。容器挂载配置文件，数据卷持久化。
- **D8 — Grafana 容器配置**：provisioning 方式自动加 datasource（Prometheus）+ 2 个 dashboard JSON：
  - `service_overview.json`：9 服务的 HTTP 请求速率/延迟/错误率 + /healthz 状态
  - `business_metrics.json`：crawl/analyze/generate/compose/pipeline 业务流量 + AI 模型用量
- **D9 — Jaeger 容器**：用 `jaegertracing/all-in-one` 镜像（OTLP gRPC 4317 + UI 16686）。9 服务通过 OTLP exporter 上报。
- **D10 — 告警规则**：新建 `observability/alerts.yml`，基础规则：服务宕机（up==0 持续 1m）、高错误率（5xx > 5% 持续 5m）、爬虫失败率（crawl_jobs_total{status="failed"} rate > 50% 持续 10m）。Prometheus alertmanager 本轮不引入（只配规则， Grafana 告警或后续加）。

---

## 3. 实施步骤

### Step 31：Phase 6 收尾 + mcp_gateway 路由冲突修复
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\mcp_gateway\routes.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\mcp_gateway\main.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\tests\test_phase6_health.py`

**routes.py 变更**：删除 lines 85-97 的 `/healthz`/`/readyz`/`/metrics` 手写路由。将 `active_sessions` 业务数据迁移为 `/business_metrics` 端点：
```python
@router.get("/business_metrics", summary="MCP business metrics")
async def business_metrics():
    return {"service": "mcp-gateway", "active_sessions": len(_active_sessions)}
```

**main.py 变更**：在 `setup_metrics(app, SERVICE_NAME)` 后追加 Gauge 注册：
```python
from prometheus_client import Gauge
mcp_active_sessions = Gauge("mcp_active_sessions", "Active MCP SSE sessions")
```
并在 SSE session 创建/销毁时 `mcp_active_sessions.inc()`/`dec()`（routes.py 内）。

**test_phase6_health.py 变更**：2 个失败测试改为 TestClient 实际请求，用 monkeypatch mock lifespan 依赖：
```python
def test_crawl_scheduler_main_exposes_health_and_metrics(monkeypatch):
    # mock lifespan dependencies to avoid real DB connection
    from common_sdk.config import config_manager
    monkeypatch.setenv("REDIS_HOST", "localhost")
    # mock the clients' create_pool/connect/ping
    from project.backend.crawl_scheduler import main as mod
    # ... patch get_redis_client/get_mysql_client
    client = TestClient(mod.app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/healthz").json()["service"] == "crawl-scheduler"
```

**验证**：`python -m pytest tests/test_phase6_health.py tests/test_phase6_metrics.py -v` 全 9 测试通过。

---

### Step 32：自定义业务指标框架
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\utils\common_sdk\business_metrics.py`

**business_metrics.py 设计**：
```python
"""Business metrics registry (Counters/Histograms).

All metrics are registered with the default prometheus_client registry,
so they are automatically exposed on /metrics alongside the HTTP
auto-instrumentation metrics.
"""
from __future__ import annotations
from prometheus_client import Counter, Histogram

# Crawl
crawl_jobs_total = Counter(
    "crawl_jobs_total",
    "Total crawl jobs processed",
    ["platform", "status"],
)
crawl_products_found = Histogram(
    "crawl_products_found",
    "Products found per crawl job",
    ["platform"],
    buckets=(0, 10, 50, 100, 500, 1000, 5000),
)

# AI Generation
ai_generation_requests_total = Counter(
    "ai_generation_requests_total",
    "Total AI generation requests",
    ["adapter_type", "model", "status"],
)
ai_generation_duration_seconds = Histogram(
    "ai_generation_duration_seconds",
    "AI generation duration in seconds",
    ["adapter_type"],
    buckets=(0.5, 1, 5, 10, 30, 60, 120, 300),
)

# Video Compose
video_compose_jobs_total = Counter(
    "video_compose_jobs_total",
    "Total video compose jobs",
    ["status"],
)

# Publish
publish_jobs_total = Counter(
    "publish_jobs_total",
    "Total publish jobs",
    ["platform", "status"],
)

# Pipeline
pipeline_runs_total = Counter(
    "pipeline_runs_total",
    "Total pipeline runs",
    ["status"],
)
```

**验证**：单测 `tests/test_phase7_business_metrics.py`——导入 business_metrics，Counter.inc() 后 `/metrics` 文本含指标名。

---

### Step 33：业务指标埋点（5 服务）
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\crawl_scheduler\tasks.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\ai_generation\tasks.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\video_composer\tasks.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\publish_dispatcher\tasks.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\pipeline_orchestrator\routes.py`（或 tasks.py 若有）

**埋点模式（以 crawl_scheduler/tasks.py 为例）**：
```python
from common_sdk.business_metrics import crawl_jobs_total, crawl_products_found

@create_task("execute_crawl_job", queue="crawl_queue")
def execute_crawl_job(self, task_id, platform, keyword, ...):
    try:
        # ... existing logic ...
        result, persisted = asyncio.run(_run())
        crawl_jobs_total.labels(platform=platform, status="success").inc()
        crawl_products_found.labels(platform=platform).observe(result.total_found)
        return {...}
    except Exception as e:
        crawl_jobs_total.labels(platform=platform, status="failed").inc()
        raise
```

**各服务埋点位置**：
| 服务 | 文件 | 埋点 |
|---|---|---|
| crawl_scheduler | tasks.py execute_crawl_job | crawl_jobs_total + crawl_products_found |
| ai_generation | tasks.py 各 adapter 任务 | ai_generation_requests_total + ai_generation_duration_seconds |
| video_composer | tasks.py compose 任务 | video_compose_jobs_total |
| publish_dispatcher | tasks.py publish 任务 | publish_jobs_total |
| pipeline_orchestrator | routes.py pipeline 触发 | pipeline_runs_total |

**验证**：单测——mock 任务执行，断言 Counter 值递增。

---

### Step 34：OpenTelemetry 分布式追踪
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\requirements.txt`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\utils\common_sdk\tracing.py`
- 修改 9 个 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\*\main.py`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\utils\common_sdk\http_client.py`

**requirements.txt 追加**：
```
opentelemetry-api>=1.24.0
opentelemetry-sdk>=1.24.0
opentelemetry-exporter-otlp>=1.24.0
opentelemetry-instrumentation-fastapi>=0.45b0
opentelemetry-instrumentation-httpx>=0.45b0
opentelemetry-instrumentation-aiomysql>=0.45b0
opentelemetry-instrumentation-redis>=0.45b0
```

**tracing.py 设计**：
```python
"""OpenTelemetry tracing setup.

Auto-instruments FastAPI + httpx + aiomysql + redis and exports spans
via OTLP to Jaeger (OTEL_EXPORTER_OTLP_ENDPOINT env var).
Disabled when OTEL_ENABLED != "true" (fail-soft).
"""
from __future__ import annotations
import os
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.aiomysql import AioMySQLInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

_instrumented = False

def setup_tracing(app: FastAPI, service_name: str) -> None:
    global _instrumented
    if os.getenv("OTEL_ENABLED", "false").lower() != "true":
        return  # fail-soft: tracing disabled by default
    if _instrumented:
        FastAPIInstrumentor.instrument_app(app)
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    AioMySQLInstrumentor().instrument()
    RedisInstrumentor().instrument()
    _instrumented = True
```

**9 服务 main.py 变更**：在 `setup_metrics(app, ...)` 后追加 `setup_tracing(app, SERVICE_NAME)`。

**http_client.py 变更**：OTEL httpx instrumentation 自动注入 `traceparent` header（W3C），无需手动改。但需确认 `HTTPXClientInstrumentor().instrument()` 全局生效——它在 tracing.py 中调用一次即可。

**验证**：单测 `tests/test_phase7_tracing.py`——`OTEL_ENABLED=false` 时 setup_tracing no-op；`OTEL_ENABLED=true` + mock OTLP exporter 时 app 含 instrumentation。

---

### Step 35：Prometheus + Grafana + Jaeger 容器
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\prometheus.yml`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\alerts.yml`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\grafana\provisioning\datasources\prometheus.yml`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\grafana\provisioning\dashboards\dashboards.yml`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\grafana\dashboards\service_overview.json`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\observability\grafana\dashboards\business_metrics.json`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\docker-compose.yml`

**prometheus.yml**：
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
rule_files:
  - /etc/prometheus/alerts.yml
scrape_configs:
  - job_name: "prodvideo-backends"
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

**alerts.yml**：
```yaml
groups:
  - name: prodvideo-alerts
    rules:
      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Service {{ $labels.instance }} down"
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High 5xx error rate on {{ $labels.handler }}"
      - alert: CrawlFailureRate
        expr: rate(crawl_jobs_total{status="failed"}[10m]) / rate(crawl_jobs_total[10m]) > 0.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Crawl failure rate > 50% for {{ $labels.platform }}"
```

**grafana datasources/prometheus.yml**：
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

**grafana dashboards/dashboards.yml**：
```yaml
apiVersion: 1
providers:
  - name: "ProdVideo Dashboards"
    folder: ""
    type: file
    options:
      path: /var/lib/grafana/dashboards
```

**service_overview.json**：Grafana dashboard JSON 含 panels：
- 9 服务 HTTP 请求速率（QPS）—— `rate(http_requests_total[5m])` by handler
- 9 服务 HTTP 延迟 P95 —— `histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))`
- 9 服务错误率 —— `rate(http_requests_total{status=~"5.."}[5m])`
- 服务存活状态 —— `up`

**business_metrics.json**：Grafana dashboard JSON 含 panels：
- 爬虫任务速率（按平台）—— `rate(crawl_jobs_total[5m])` by platform, status
- 爬虫发现商品数分布 —— `rate(crawl_products_found_sum[5m])`
- AI 生成请求速率（按类型）—— `rate(ai_generation_requests_total[5m])` by adapter_type
- AI 生成延迟 P95 —— `histogram_quantile(0.95, rate(ai_generation_duration_seconds_bucket[5m]))`
- 视频合成/发布/Pipeline 任务速率

**docker-compose.yml 追加 3 服务**（在 `frontend:` 之后、`volumes:` 之前）：
```yaml
  prometheus:
    image: prom/prometheus:latest
    container_name: prodvideo-prometheus
    restart: unless-stopped
    volumes:
      - ./observability/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./observability/alerts.yml:/etc/prometheus/alerts.yml
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    depends_on:
      - mcp-gateway

  grafana:
    image: grafana/grafana:latest
    container_name: prodvideo-grafana
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning
      - ./observability/grafana/dashboards:/var/lib/grafana/dashboards
      - grafana_data:/var/lib/grafana
    ports:
      - "3000:3000"
    depends_on:
      - prometheus

  jaeger:
    image: jaegertracing/all-in-one:1.55
    container_name: prodvideo-jaeger
    restart: unless-stopped
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
    ports:
      - "16686:16686"   # UI
      - "4317:4317"     # OTLP gRPC
```

**volumes 追加**：`prometheus_data:`、`grafana_data:`

**docker-compose.yml env 锚点追加**：`x-backend-env` 加 `OTEL_ENABLED: "true"` 和 `OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4317`。

**验证**：`docker compose config` YAML 合法；`docker compose up -d prometheus grafana jaeger` 启动后 http://localhost:3000 可登录、http://localhost:9090/targets 显示 9 后端被 scrape、http://localhost:16686 可访问。

---

### Step 36：文档更新
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\README.md`
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\doc\observability.md`

**README.md 变更**：
- 端口表追加：Prometheus 9090、Grafana 3000、Jaeger 16686
- "健康检查与可观测性"小节追加：Grafana 看板访问、Jaeger 追踪访问、默认账号 admin/admin

**observability.md 变更**：
- 删除"Prometheus + Grafana 容器将在 Phase 7 引入"占位，替换为实际使用说明
- 删除"链路追踪（Phase 7）"占位，替换为 Jaeger + OpenTelemetry 章节
- 追加"业务指标"小节：列出 7 个自定义 Counter/Histogram 及含义
- 追加"告警规则"小节：列出 3 条规则

---

### Step 37：Phase 7 测试 + 全量回归
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\tests\test_phase7_business_metrics.py`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\tests\test_phase7_tracing.py`

**test_phase7_business_metrics.py**（4 测试）：
1. `test_business_metrics_imports_succeed` — 导入 business_metrics 无 ImportError
2. `test_crawl_jobs_counter_increments` — `crawl_jobs_total.labels(platform="douyin", status="success").inc()` 后值递增
3. `test_ai_generation_histogram_observes` — `ai_generation_duration_seconds.labels(adapter_type="llm").observe(1.5)` 无异常
4. `test_business_metrics_exposed_on_metrics_endpoint` — 构造 FastAPI app + setup_metrics + Counter.inc()，TestClient GET `/metrics` 文本含 `crawl_jobs_total`

**test_phase7_tracing.py**（3 测试）：
1. `test_setup_tracing_noop_when_disabled` — `OTEL_ENABLED=false` 时 setup_tracing 不抛异常、不设置 tracer provider
2. `test_setup_tracing_instruments_app_when_enabled` — `OTEL_ENABLED=true` + mock OTLPSpanExporter，setup_tracing 后 app 被 instrumented（检查 app 属性或无异常）
3. `test_tracing_failsoft_on_missing_jaeger` — `OTEL_ENABLED=true` 但 endpoint 不可达，setup_tracing 不阻塞（BatchSpanProcessor 异步，不阻塞启动）

**回归**：`python -m pytest tests/ -v --tb=short`，预期 131（Phase 6 收尾后）+ 7 = 138 passed。

---

## 4. 假设

- A1：`opentelemetry-instrumentation-aiomysql` 包存在且兼容 aiomysql 0.2.x（若不存在则降级为只 instrument FastAPI + httpx + redis）。
- A2：Jaeger all-in-one 镜像的 OTLP gRPC 4317 端口默认开启（`COLLECTOR_OTLP_ENABLED=true`）。
- A3：Prometheus scrape 9 后端容器名（`crawl-scheduler:8001` 等）在 docker-compose 网络内可解析（Phase 6 已验证服务间网络）。
- A4：Grafana provisioning 方式加载 dashboard JSON 无需手动导入（dashboard JSON 格式正确）。
- A5：Phase 6 残留的 2 个失败测试可通过 monkeypatch mock lifespan 依赖解决（TestClient 启动 app 时触发 lifespan，需 mock create_pool/connect）。
- A6：mcp_gateway 的 active_sessions Gauge 在 SSE session 创建/销毁时 inc/dec 不会产生竞态（asyncio 单线程，安全）。
- A7：OTEL_ENABLED 默认 "false" 在开发环境（无 Jaeger）不阻塞；docker-compose env 锚点设 "true" 启用。

---

## 5. 验证步骤

1. **Phase 6 收尾**：`python -m pytest tests/test_phase6_health.py tests/test_phase6_metrics.py -v` → 9 passed（原 7 + 修复 2）。
2. **业务指标单测**：`python -m pytest tests/test_phase7_business_metrics.py -v` → 4 passed。
3. **追踪单测**：`python -m pytest tests/test_phase7_tracing.py -v` → 3 passed。
4. **全量回归**：`python -m pytest tests/ -v --tb=short` → 138 passed, 0 failed。
5. **导入完整性**：`python -c "import project.backend.X.main"` 9 服务全部成功（含 tracing import）。
6. **mcp_gateway 路由修复**：TestClient GET mcp_gateway `/healthz` 返回 `{"status":"ok","service":"mcp-gateway"}`（含 service 字段，证明工厂版生效非手写版）。
7. **Docker Compose 配置**：`docker compose config` YAML 合法，含 28 服务（25 + prometheus + grafana + jaeger）。
8. **Prometheus 配置**：`python -c "import yaml; yaml.safe_load(open('observability/prometheus.yml'))"` 通过。
9. **Grafana dashboard JSON**：2 个 JSON 文件 `python -c "import json; json.load(open('...'))"` 通过。
10. **可观测性栈启动**（可选，用户本地）：`docker compose up -d prometheus grafana jaeger` 后：
    - http://localhost:3000 登录 admin/admin，看到 2 个 dashboard
    - http://localhost:9090/targets 显示 9 后端 UP
    - http://localhost:16686 可访问 Jaeger UI

---

## 6. 范围外

- Kong 声明式路由配置（Phase 8）
- Nacos 服务注册（Phase 8）
- AWS SigV4 签名 / Amazon PA-API（Phase 8）
- 生产级 worker 拆分 + autoscale（Phase 8）
- K8s manifest / Helm chart（Phase 8+）
- Playwright E2E 浏览器集成测试（Phase 8）
- Alertmanager 容器（本轮只配 Prometheus 规则，告警通知留 Phase 8）
- 自定义 Grafana 告警通道（本轮只 provisioning dashboard）

---

## 7. 任务清单（执行顺序）

| # | Step | 描述 | 预计文件数 |
|---|---|---|---|
| 1 | Step 31 | Phase 6 收尾 + mcp_gateway 路由冲突修复 | 3 改 |
| 2 | Step 32 | 自定义业务指标框架 | 1 新 |
| 3 | Step 33 | 业务指标埋点（5 服务） | 5 改 |
| 4 | Step 34 | OpenTelemetry 分布式追踪 | 1 改 + 1 新 + 9 改 + 1 改 |
| 5 | Step 35 | Prometheus + Grafana + Jaeger 容器 | 6 新 + 1 改 |
| 6 | Step 36 | 文档更新 | 2 改 |
| 7 | Step 37 | Phase 7 测试 + 全量回归 | 2 新 |

**总计**：约 10 新文件 + 21 改文件。
