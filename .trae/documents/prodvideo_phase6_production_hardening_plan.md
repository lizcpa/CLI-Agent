# Phase 6：生产强化（Production Hardening）

> 目标：让 ProdVideo AI Factory **真正可部署运行**——一键 `docker compose up` 启动全栈（9 后端 + 1 Celery worker + 前端 + 基础设施），统一健康检查/指标端点，CI 自动跑测试，文档闭环。
>
> 完成后进入 Phase 7（待用户定义方向）。

---

## 1. 当前状态分析（Phase 1 探索结论）

### 1.1 已就绪
- **基础设施 docker-compose.yml**：MySQL/MongoDB/Redis/MinIO/RabbitMQ/Kong/Nacos/Vault 全部含 healthcheck，可一键起。
- **9 个后端微服务**：全部有 `main.py` + `routes.py` + `config.py`；7 个有 `tasks.py`（mcp_gateway/web_backend 是路由网关本不需）。
- **common_sdk**：logger（structlog JSON）、config、auth、http_client、vault_client、nacos_client、content_safety 全为真实实现。
- **db_clients**：MySQL/Redis/MinIO/MongoDB 全为真实异步实现，从 env 读 host/port（`MYSQL_HOST`/`REDIS_HOST`/`MINIO_ENDPOINT`/`MONGODB_HOST`）。
- **Celery**：`celery_app.py` 定义 6 队列 + `BaseTask` + `create_task`/`create_periodic_task` 装饰器，Redis 结果后端。
- **前端**：Vue 3 + Vite + element-plus，8 个 view 真实存在，`dist/` 已有构建产物。
- **测试**：125 个测试全通过；14 个测试文件。
- **数据库 schema**：`database/init.sql` 含 9 张表，包括 `platform_config`/`platform_authorizations`/`products`/`generation_pipelines`。

### 1.2 关键 Gap（Phase 6 必须解决）
| # | Gap | 影响 |
|---|---|---|
| G1 | 5 服务 `/healthz` 为 stub（`{"status":"ok"}` 无 service 字段）；`pipeline_orchestrator` 完全无 `/metrics`；多处 `/readyz` 在探活时 `create_pool`（反模式）；`ai_generation`/`video_composer` lifespan 未关 MinIO | 健康检查不一致，K8s/Compose 探针不可靠 |
| G2 | `crawl_scheduler/main.py` 已 `import prometheus_fastapi_instrumentator` 但 **requirements.txt 未列该依赖**；其他 8 服务无 Prometheus 指标 | 启动即 ImportError；无可观测性 |
| G3 | **全项目零 Dockerfile**；docker-compose 仅含基础设施，9 服务 + worker + 前端未容器化 | 无法一键部署 |
| G4 | **无 Celery worker 启动入口**（无 `worker.py`、无 worker Dockerfile、compose 无 worker 服务） | 生产环境无法跑后台任务 |
| G5 | **无 CI 配置**（无 `.github/workflows/`、无 `.gitlab-ci.yml`） | 无自动化测试/质量门 |
| G6 | **无 pytest.ini / conftest.py / .coveragerc**；测试靠每个文件 `sys.path.insert` 重复 | 测试配置缺失，无覆盖率报告 |
| G7 | 前端无 Dockerfile / nginx.conf | 前端不可部署 |
| G8 | 根目录 `nul` 0 字节文件（Windows 保留名）导致 ripgrep 报 "函数不正确" | 工具链受损 |
| G9 | README 部署指南未写（链接标注"待创建"） | 可运维性差 |

### 1.3 显式 DEFER 到 Phase 7（不在本轮范围）
- **OpenTelemetry / Jaeger 分布式追踪**：集成重，需 instrumentation 全链路改造。
- **Kong 声明式路由配置**：9 服务路由 + JWT 插件 + 限流插件，独立大块；当前直接访问 8000-8007 端口可用。
- **Nacos 服务注册**：`.env` 默认 `NACOS_ENABLED=false`，fail-soft 已工作；服务注册非阻塞项。
- **自定义业务指标（Counter/Histogram）**：本轮只做 auto-instrumentation（HTTP latency/status/rate）；自定义业务指标按需在 Phase 7 加。
- **真实 Playwright 浏览器集成测试**：当前全 mock，够用。

---

## 2. 决策（D1-D12）

- **D1 — 指标库**：用 `prometheus-fastapi-instrumentator>=6.1.0`（crawl_scheduler 已 import，统一之）。不引入 opentelemetry。
- **D2 — 健康检查工厂**：新建 `utils/common_sdk/health.py`，导出 `build_health_router(service_name, check_ready)` 工厂函数，返回 FastAPI APIRouter 含 `/healthz`+`/readyz`。`/healthz` 恒返回 200 + service 名（进程存活即 ok）；`/readyz` 调用传入的 `check_ready()` 协程，返回 200 或 503 + 各依赖状态。`/metrics` 由 Instrumentator 自动暴露，不手写路由。
- **D3 — 统一改造范围**：9 个服务的 `main.py` 全部改用 `build_health_router` + `setup_metrics`；删除手写的 `/healthz`/`/readyz`/`/metrics` 路由（product_analyzer 的业务 metrics 路由改路径为 `/business_metrics` 保留，避免与 Instrumentator 的 `/metrics` 冲突）。
- **D4 — MinIO 关闭修复**：`ai_generation/main.py`、`video_composer/main.py` lifespan shutdown 加 `get_minio_client().close()`（若 MinioClient 无 `close` 方法则跳过——MinIO 是同步客户端无连接池，无需关闭，仅在 logger 记录）。**核实后若 MinioClient 确无连接态则不改**（避免无意义改动）。
- **D5 — Dockerfile 策略**：3 个统一 Dockerfile：
  - `Dockerfile.backend`：基于 `python:3.11-slim`，装 requirements.txt，`ARG SERVICE_DIR`，`CMD uvicorn project.backend.${SERVICE_DIR}.main:app --host 0.0.0.0 --port ${SERVICE_PORT}`。9 服务共用。
  - `Dockerfile.worker`：同基础镜像，`CMD celery -A mq_clients.celery_app:celery_app worker -Q ${QUEUES} --loglevel=info`。一个镜像跑所有队列。
  - `Dockerfile.frontend`：多阶段——`node:20-alpine` 构建 → `nginx:alpine` + `nginx.conf` 提供 SPA。
- **D6 — docker-compose 扩展**：新增 11 个服务（9 后端 + 1 worker + 1 前端），每个后端 `depends_on: mysql(service_healthy)/redis(service_healthy)`，注入 env（`MYSQL_HOST=mysql` 等），暴露端口 8000-8007 + 8008（orchestrator）+ 1001（前端）。Worker `depends_on: rabbitmq/redis`。后端 healthcheck 用 `/healthz`。
- **D7 — Worker 部署形态**：**单个 worker 容器**消费所有 6 队列（`-Q crawl_queue,analyze_queue,ai_queue,compose_queue,publish_queue,orchestrator_queue`），开发环境够用；文档注明生产应拆分。
- **D8 — CI**：GitHub Actions，`.github/workflows/ci.yml`，单 workflow 含 2 job：`lint`（ruff，若未装则用 `python -m py_compile` 全量编译）+ `test`（`pytest tests/ --tb=short`，Python 3.11，缓存 pip）。不上传覆盖率到第三方（只本地生成）。
- **D9 — pytest 配置**：根目录 `pytest.ini` 设 `asyncio_mode=auto`、`testpaths=tests`、`python_paths=utils .`；`tests/conftest.py` 抽出公共 `sys.path.insert`（消除每个测试文件重复的 2 行）；`.coveragerc` omit `.venv/`/`tests/`/`*/__pycache__/`。
- **D10 — `nul` 文件清理**：用 `Remove-Item -LiteralPath '\\?\C:\...\nul'`（Windows 保留名需 `\\?\` 前缀）删除。验证后 `grep` 不再报错。
- **D11 — 前端 nginx.conf**：标准 SPA 配置——`try_files $uri $uri/ /index.html`，`/api/` 反代到 `web_backend:8007`，gzip on。
- **D12 — 文档**：更新 `README.md` 部署章节（替换"待创建"占位）；新建 `doc/deployment_guide.md`（一键启动、env 变量、端口表、健康检查、排障）；新建 `doc/observability.md`（指标端点、健康端点、日志格式）。

---

## 3. 实施步骤

### Step 23：测试基础设施 hygiene
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\pytest.ini`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\tests\conftest.py`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\.coveragerc`
- 删除 `c:\Users\29048\PycharmProjects\PythonProject1\nul`

**pytest.ini 内容**：
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_paths = utils .
addopts = -ra --tb=short
```

**tests/conftest.py 内容**：抽公共 sys.path 注入（`utils/` + 项目根），让 14 个测试文件可移除各自的 `sys.path.insert`（本轮只新增 conftest，不强制改老文件——避免回归）。提供 `pytest_configure` hook 记录开始时间。

**.coveragerc 内容**：
```ini
[run]
source = utils,project
omit = */tests/*,*/.venv/*,*/__pycache__/*,*/site-packages/*
[report]
show_missing = True
```

**删除 nul**：PowerShell `Remove-Item -LiteralPath '\\?\C:\Users\29048\PycharmProjects\PythonProject1\nul' -Force`。

**验证**：`python -m pytest tests/ -q` 仍 125 passed；`grep -r "TODO" project/backend` 不再报 "函数不正确"。

---

### Step 24：Prometheus 指标标准化
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\requirements.txt`（加 `prometheus-fastapi-instrumentator>=6.1.0`）
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\utils\common_sdk\metrics.py`

**metrics.py 设计**：
```python
from __future__ import annotations
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

def setup_metrics(app: FastAPI, service_name: str) -> None:
    """Auto-expose /metrics with default HTTP latency/status/rate metrics."""
    Instrumentator(
        excluded_handlers=["/healthz", "/readyz"],  # 不抓探针流量
        env_var_name="PROMETHEUS_METRICS_ENABLED",  # 默认开，可 env 关
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
```

**安装依赖**：`pip install prometheus-fastapi-instrumentator>=6.1.0`。

**验证**：单测 `tests/test_phase6_metrics.py`——构造空 FastAPI app + `setup_metrics`，TestClient GET `/metrics` 返回 200 + text/plain 含 `http_requests_total`。

---

### Step 25：健康检查工厂 + 9 服务标准化
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\utils\common_sdk\health.py`
- 修改 9 个 `c:\Users\29048\PycharmProjects\PythonProject1\project\backend\*\main.py`

**health.py 设计**：
```python
from __future__ import annotations
from typing import Awaitable, Callable
from fastapi import APIRouter
from fastapi.responses import JSONResponse

def build_health_router(
    service_name: str,
    check_ready: Callable[[], Awaitable[dict[str, bool]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": service_name}

    @router.get("/readyz")
    async def readyz():
        if check_ready is None:
            return {"status": "ready", "service": service_name}
        checks = await check_ready()
        all_ok = all(checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={
                "status": "ready" if all_ok else "not_ready",
                "service": service_name,
                "checks": {k: "ok" if v else "fail" for k, v in checks.items()},
            },
        )
    return router
```

**9 服务改造模式（以 `crawl_scheduler/main.py` 为例）**：
- 删除手写 `/healthz`/`/readyz`/`/metrics` 路由。
- 删除 `Instrumentator()` 直接调用，改为 `from common_sdk.metrics import setup_metrics` + `setup_metrics(app, SERVICE_NAME)`。
- `from common_sdk.health import build_health_router` + `app.include_router(build_health_router(SERVICE_NAME, _check_ready))`。
- 定义 `_check_ready()` 协程返回 `{"redis": await get_redis_client().ping(), "mysql": await get_mysql_client().ping()}`（按服务实际依赖选）。

**各服务 ready 依赖矩阵**（探活哪些客户端）：
| 服务 | redis | mysql | minio | mongodb |
|---|---|---|---|---|
| mcp_gateway | - | - | - | - |
| crawl_scheduler | ✓ | ✓ | - | - |
| product_analyzer | ✓ | ✓ | - | - |
| ai_generation | ✓ | ✓ | ✓ | - |
| video_composer | - | ✓ | ✓ | - |
| publish_dispatcher | ✓ | ✓ | - | - |
| asset_manager | - | ✓ | ✓ | - |
| web_backend | ✓ | ✓ | - | - |
| pipeline_orchestrator | ✓ | ✓ | - | - |

mcp_gateway `check_ready=None`（无外部依赖，恒 ready）。

**product_analyzer 业务指标**：原 `/metrics` 路由改名为 `/business_metrics`（保留业务数据），`/metrics` 让给 Instrumentator。

**MinIO close 核实**：读 `utils/db_clients/minio.py` 确认 MinioClient 是否有连接态。若无 `close()` 方法则 D4 跳过；若有则补 `await get_minio_client().close()`。

**验证**：`tests/test_phase6_health.py`——4 个测试：
1. `test_build_health_router_healthz_always_ok`
2. `test_build_health_router_readyz_503_when_dependency_fail`（mock check_ready 返回 `{"redis": False}`）
3. `test_build_health_router_readyz_200_when_all_ok`
4. `test_crawl_scheduler_main_uses_factory`（import main 模块，断言 app 有 `/metrics` 路由 + `/healthz` 返回 service 名）

---

### Step 26：Dockerfile（3 个）
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\Dockerfile.backend`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\Dockerfile.worker`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\Dockerfile.frontend`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\project\frontend\nginx.conf`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\.dockerignore`

**Dockerfile.backend**：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# playwright 需要的浏览器二进制可选；本轮不装（爬虫用真实浏览器时另装）
COPY utils/ ./utils/
COPY project/ ./project/
COPY database/ ./database/
ARG SERVICE_DIR
ARG SERVICE_PORT
ENV SERVICE_DIR=${SERVICE_DIR}
ENV SERVICE_PORT=${SERVICE_PORT}
ENV PYTHONPATH=/app/utils:/app
EXPOSE ${SERVICE_PORT}
CMD ["sh", "-c", "uvicorn project.backend.${SERVICE_DIR}.main:app --host 0.0.0.0 --port ${SERVICE_PORT}"]
```

**Dockerfile.worker**：
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY utils/ ./utils/
COPY project/ ./project/
ENV PYTHONPATH=/app/utils:/app
ARG QUEUES=crawl_queue,analyze_queue,ai_queue,compose_queue,publish_queue,orchestrator_queue
ENV QUEUES=${QUEUES}
CMD ["sh", "-c", "celery -A mq_clients.celery_app:celery_app worker -Q ${QUEUES} --loglevel=info --concurrency=4"]
```

**Dockerfile.frontend**（多阶段）：
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY project/frontend/package*.json ./
RUN npm ci
COPY project/frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY project/frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

**nginx.conf**：
```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;
    gzip on;
    gzip_types text/css application/javascript application/json;

    location /api/ {
        proxy_pass http://web_backend:8007;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

**.dockerignore**：
```
.venv/
.git/
.idea/
__pycache__/
*.pyc
.pytest_cache/
.trae/
nul
```

---

### Step 27：docker-compose.yml 扩展（追加 11 服务）
**文件**：修改 `c:\Users\29048\PycharmProjects\PythonProject1\docker-compose.yml`

**新增服务**（追加到 `services:` 下，基础设施不动）：
- 9 后端：`mcp-gateway`(8000)、`crawl-scheduler`(8001)、`product-analyzer`(8002)、`ai-generation`(8003)、`video-composer`(8004)、`publish-dispatcher`(8005)、`asset-manager`(8006)、`web-backend`(8007)、`pipeline-orchestrator`(8008)
- 1 worker：`celery-worker`
- 1 前端：`frontend`(1001→80)

**每个后端服务模板**（以 crawl-scheduler 为例）：
```yaml
  crawl-scheduler:
    build:
      context: .
      dockerfile: Dockerfile.backend
      args:
        SERVICE_DIR: crawl_scheduler
        SERVICE_PORT: "8001"
    container_name: prodvideo-crawl-scheduler
    restart: unless-stopped
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    environment:
      MYSQL_HOST: mysql
      MYSQL_PORT: "3306"
      MYSQL_USER: dev_user
      MYSQL_PASSWORD: dev_pass_2024
      MYSQL_DATABASE: prodvideo
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_PASSWORD: dev_redis_2024
      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin2024
      MONGODB_HOST: mongodb
      MONGODB_PORT: "27017"
      MONGODB_DATABASE: prodvideo
      CELERY_BROKER_URL: amqp://guest:guest@rabbitmq:5672//
      CELERY_RESULT_BACKEND: redis://:dev_redis_2024@redis:6379/0
      INTERNAL_JWT_SECRET: dev-jwt-secret-prodvideofactory-2024
      NACOS_ENABLED: "false"
      VAULT_ENABLED: "false"
      CONTENT_SAFETY_ENABLED: "false"
    ports:
      - "8001:8001"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8001/healthz')"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
```

**worker 服务**：
```yaml
  celery-worker:
    build:
      context: .
      dockerfile: Dockerfile.worker
    container_name: prodvideo-celery-worker
    restart: unless-stopped
    depends_on:
      rabbitmq:
        condition: service_healthy
      redis:
        condition: service_healthy
      mysql:
        condition: service_healthy
    environment:
      # 同上 env 块
      MYSQL_HOST: mysql
      REDIS_HOST: redis
      CELERY_BROKER_URL: amqp://guest:guest@rabbitmq:5672//
      CELERY_RESULT_BACKEND: redis://:dev_redis_2024@redis:6379/0
      # ... 其余同后端
```

**前端服务**：
```yaml
  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    container_name: prodvideo-frontend
    restart: unless-stopped
    depends_on:
      web-backend:
        condition: service_healthy
    ports:
      - "1001:80"
```

**端口冲突说明**：mcp_gateway 文档端口 8000 与 Kong proxy 8000 冲突。处理：Kong 保留 8000/8443/8001/8002；mcp_gateway 容器内部用 8000 但 compose 映射为 `8010:8000`（文档注明访问 8010，或经 Kong 代理）。**本轮决策：mcp_gateway 映射 `8010:8000`，README 更新端口表**。

---

### Step 28：CI（GitHub Actions）
**文件**：新建 `c:\Users\29048\PycharmProjects\PythonProject1\.github\workflows\ci.yml`

**ci.yml 设计**：
```yaml
name: CI
on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-asyncio pytest-cov
      - run: python -m pytest tests/ -ra --tb=short --cov=utils --cov=project --cov-report=term-missing
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff
      - run: ruff check utils/ project/ || true  # 非 blocking，先报告
```

**说明**：lint 用 `|| true` 非阻塞（老代码可能有格式问题，渐进收紧）；test 阻塞。

---

### Step 29：文档
**文件**：
- 修改 `c:\Users\29048\PycharmProjects\PythonProject1\README.md`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\doc\deployment_guide.md`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\doc\observability.md`

**README.md 变更**：
- 替换"启动顺序"段为"一键启动"：`docker compose up -d` 全栈起；附端口表（含 8010 mcp_gateway 调整）。
- 替换文档索引底部"待创建"为实际链接。
- 新增"健康检查"小节：列出 9 服务 `/healthz`/`/readyz`/`/metrics` 端点。

**deployment_guide.md 内容**：
- 前置要求（Docker Desktop / Python 3.11）
- 一键启动：`docker compose up -d`，等所有 healthcheck pass（`docker compose ps`）
- 端口表（9 后端 + 前端 + 基础设施）
- 环境变量说明（.env 关键项）
- 单服务启动（开发模式）：`uvicorn project.backend.xxx.main:app --port xxxx --reload`
- 单 worker 启动：`celery -A mq_clients.celery_app:celery_app worker -Q crawl_queue,...`
- 排障：MySQL 连不上 / Redis 密码 / MinIO bucket / Vault 关闭 / Nacos 关闭
- 停止：`docker compose down`（-v 清数据卷）

**observability.md 内容**：
- 日志：structlog JSON 格式，ISO 时间戳，LOG_LEVEL 环境变量
- 指标：每服务 `/metrics` 暴露 Prometheus 格式（HTTP latency/status/rate）；product_analyzer 额外 `/business_metrics`
- 健康：`/healthz`（liveness）+ `/readyz`（readiness，含依赖状态）
- 推荐集成：Phase 7 加 Prometheus + Grafana 容器（本轮不引入）

---

### Step 30：Phase 6 单元测试 + 全量回归
**文件**：
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\tests\test_phase6_health.py`
- 新建 `c:\Users\29048\PycharmProjects\PythonProject1\tests\test_phase6_metrics.py`

**test_phase6_health.py**（4 测试）：
1. `test_build_health_router_healthz_always_ok` — 无 check_ready，healthz 返回 200 + service 名
2. `test_build_health_router_readyz_200_when_all_ok` — check_ready 返回 `{"redis": True}`，readyz 200
3. `test_build_health_router_readyz_503_when_dependency_fail` — check_ready 返回 `{"redis": False}`，readyz 503 + checks 字段
4. `test_crawl_scheduler_main_uses_factory_and_metrics` — import main，TestClient GET `/healthz`/`/metrics` 都 200

**test_phase6_metrics.py**（2 测试）：
1. `test_setup_metrics_exposes_endpoint` — 空 FastAPI app + setup_metrics，TestClient GET `/metrics` 200 + content-type text/plain
2. `test_setup_metrics_excludes_health_endpoints` — GET `/healthz` 不被 instrumentator 计数（验证 excluded_handlers）

**回归**：`python -m pytest tests/ -v --tb=short`，预期 125 + 6 = 131 passed。

---

## 4. 假设

- A1：Docker Desktop 已安装并能运行（用户本地环境）。
- A2：`pip install prometheus-fastapi-instrumentator` 不会与现有依赖冲突。
- A3：9 服务的 `config.py` 中 `SERVICE_PORT` 与文档端口一致（探索确认 8001-8008，mcp_gateway 8000 因 Kong 冲突映射 8010）。
- A4：前端 `npm run build` 能成功生成 `dist/`（已有产物，假设可重建）。
- A5：删除 `nul` 文件不影响任何代码（0 字节，无 import 引用）。
- A6：MinioClient 若无 `close()` 方法则 D4 跳过（避免无意义改动；MinIO 同步客户端无连接池）。

---

## 5. 验证步骤

1. **测试**：`python -m pytest tests/ -v --tb=short` → 131 passed, 0 failed。
2. **导入完整性**：`python -c "import project.backend.crawl_scheduler.main"` 等 9 服务 main 全部 import 成功（无 ImportError）。
3. **健康端点一致性**：TestClient 对每个服务的 app GET `/healthz` 返回 `{"status":"ok","service":"..."}`；GET `/metrics` 返回 200 text/plain。
4. **Docker 构建验证**：`docker compose build` 全部成功（11 服务镜像构建）。
5. **Docker 启动验证**（可选，用户本地执行）：`docker compose up -d` 后 `docker compose ps` 全部 healthy。
6. **CI 语法**：`python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` 通过。
7. **grep 可用性**：删除 `nul` 后 `grep -r "TODO" project/` 不报 "函数不正确"。
8. **文档链接**：README 索引链接指向实际存在的文件。

---

## 6. 范围外

- OpenTelemetry / Jaeger 追踪（Phase 7）
- Kong 声明式路由配置（Phase 7）
- Nacos 服务注册（Phase 7）
- 自定义业务 Prometheus 指标（Counter/Histogram）（Phase 7）
- Prometheus + Grafana 容器（Phase 7）
- 真实 Playwright 浏览器集成测试（Phase 7）
- AWS SigV4 签名（Amazon PA-API）（Phase 7）
- 生产级 worker 拆分（按队列独立容器 + autoscale）（Phase 7）
- K8s manifest（Helm chart）（Phase 7+）

---

## 7. 任务清单（执行顺序）

| # | Step | 描述 | 预计文件数 |
|---|---|---|---|
| 1 | Step 23 | 测试 hygiene + 删 nul | 3 新 + 1 删 |
| 2 | Step 24 | Prometheus 指标标准化 | 1 改 + 1 新 |
| 3 | Step 25 | 健康检查工厂 + 9 服务改造 | 1 新 + 9 改 |
| 4 | Step 26 | 3 个 Dockerfile + nginx + .dockerignore | 5 新 |
| 5 | Step 27 | docker-compose 扩展 11 服务 | 1 改 |
| 6 | Step 28 | GitHub Actions CI | 1 新 |
| 7 | Step 29 | 文档（README + 2 新 doc） | 1 改 + 2 新 |
| 8 | Step 30 | Phase 6 测试 + 全量回归 | 2 新 |

**总计**：约 13 新文件 + 13 改文件 + 1 删文件。
