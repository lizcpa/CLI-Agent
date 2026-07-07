# Phase 8：韧性模式（Resilience Patterns）

## Context

Phase 7 让系统具备了完整的可观测性（Prometheus + Grafana + Jaeger），但也暴露了生产就绪性的关键缺口：**服务间调用没有任何韧性保护**。

当前问题（由 Explore agent 调研确认）：

1. **零重试**：全代码库无 HTTP 层重试。pipeline orchestrator 调 product-analyzer 失败一次，整个 pipeline 即死。
2. **零熔断**（服务间）：AI 适配器层有原型（`BaseModelAdapter.mark_failure`，阈值 3/冷却 300s），但服务间调用完全无熔断。
3. **三套并行内部 HTTP 实现**：`InternalHTTPClient`（无人使用）、`pipeline_orchestrator._http_post/_get`、`mcp_gateway._post/_get`——重复且不一致。
4. **每次请求新建 `httpx.AsyncClient`**：无连接池复用，无 keep-alive。
5. **无幂等性**：pipeline 任务重复提交无防护。

**目标**：引入统一韧性层（熔断器 + 重试 + 限流 + 舱壁 + 幂等），让 pipeline DAG 在下游瞬时故障时优雅降级而非整体崩溃。

---

## 进度

| Step | 状态 | 说明 |
|------|------|------|
| 38 | ✅ 完成 | `resilience.py` 已创建（CircuitBreaker / retry / RateLimiter / Bulkhead），tenacity 已安装，`__init__.py` 已导出 |
| 39 | ✅ 完成 | 升级 `InternalHTTPClient`（lazy httpx + per-target breaker/bulkhead + retry + _handle_response） |
| 40 | ✅ 完成 | 迁移 `pipeline_orchestrator`（单 asyncio.run + 幂等守卫 + finally 清理） |
| 41 | ✅ 完成 | 迁移 `mcp_gateway` tool_handlers（模块级 _http + 薄适配器） |
| 42 | ✅ 完成 | 整合 AI 适配器熔断（BaseModelAdapter 委托 breaker + ModelRouter 统一 track_failure） |
| 43 | ✅ 完成 | 韧性 Prometheus 指标（3 指标 + _get_or_create 幂等注册）+ 文档（observability.md + README.md） |
| 44 | ✅ 完成 | Phase 8 测试（15 新测试）+ 全量回归（156 passed） |

---

## 设计决策（基于代码探索更新）

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 重试库 | `tenacity>=8.2.0`（已装 9.1.4） | 社区标准，原生 async，指数退避 + 抖动 + 异常谓词 |
| D2 | 熔断器状态机 | 3-state（closed/open/half-open），进程内 | 标准模式；分布式状态留作 Phase 9+ |
| D3 | 收敛策略 | 升级 `InternalHTTPClient` 为唯一内部 HTTP 入口 | 消除三套重复实现 |
| D4 | 熔断器粒度 | per-target（按目标服务名） | orchestrator 对每个下游服务独立熔断 |
| D5 | 舱壁 | `asyncio.Semaphore`，per-target | 限制对同一下游的并发调用数 |
| D6 | 限流 | 令牌桶（in-process, async） | 用于外部 API，非内部调用 |
| D7 | 幂等 | Redis `SET NX EX` key `pipeline:active:{product_id}:{tenant_id}` | 防止 pipeline 重复提交 |
| D8 | httpx client 生命周期 | **lazy init**（首次调用创建），非构造时 | 兼容 Celery `asyncio.run()` 边界 + 测试 mock |
| D9 | 非重试异常 | 4xx（除 429）、`CircuitBreakerOpenError` | 确定性错误重试无意义 |
| D10 | 重试异常 | 5xx、429、`httpx.ConnectError`、`httpx.TimeoutException`、`ServiceException` | 瞬时错误值得重试 |
| D11 | Celery 任务结构 | **重构为单个 `asyncio.run()`** 包裹全 pipeline | 使 breaker 状态跨 stage 持久 + httpx 连接池复用 |
| D12 | ModelRouter.track_failure | **委托 `adapter.mark_failure()`**（非删除） | 探索发现 ai_generation/routes.py 有 4 处调用，非死代码 |
| D13 | BaseModelAdapter 属性 | 保留 `is_healthy`/`failure_count`/`disabled_until` 为可变实例属性 | 测试直接赋值这些属性，改为只读 property 会破坏测试 |
| D14 | http2 | 不启用（`http2=False`） | 需额外 `h2` 依赖，requirements 未包含 |

---

## 假设

- A1：`tenacity` 兼容 Python 3.12（当前环境）✅ 已验证
- A2：OTEL httpx instrumentation（Phase 7 已启用）自动注入 traceparent，`InternalHTTPClient` 无需手动传播
- A3：`InternalHTTPSyncClient`（同步版）暂不升级，保留原样
- A4：外部 AI API 调用的重试由适配器自身处理，不走 `InternalHTTPClient`

---

## Steps

### Step 39：升级 `InternalHTTPClient`

**修改文件**：`utils/common_sdk/http_client.py`

#### 39.1 新构造函数

```python
class InternalHTTPClient:
    def __init__(
        self,
        service_name: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        cb_failure_threshold: int = 3,
        cb_cooldown_seconds: float = 30.0,
        bulkhead_concurrency: int = 10,
    ) -> None:
        self._service_name = service_name
        self._timeout = timeout
        self._max_retries = max_retries
        self._cb_failure_threshold = cb_failure_threshold
        self._cb_cooldown_seconds = cb_cooldown_seconds
        self._bulkhead_concurrency = bulkhead_concurrency
        self._client: httpx.AsyncClient | None = None  # lazy
        self._breakers: dict[str, CircuitBreaker] = {}
        self._bulkheads: dict[str, Bulkhead] = {}
        self._jwt_secret = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret")
        self._tenant_id = config_manager.get("TENANT_ID", "default")
```

**关键变更**：删除 `base_url` 参数（探索确认无调用方使用），调用方自行构建完整 URL。

#### 39.2 lazy httpx.AsyncClient

```python
def _ensure_client(self) -> httpx.AsyncClient:
    if self._client is None:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return self._client

async def close(self) -> None:
    if self._client is not None:
        await self._client.aclose()
        self._client = None
```

**理由**：Celery 任务用 `asyncio.run()` 创建临时事件循环，构造时创建的 client 会绑定到已关闭的 loop。lazy init 在实际调用时（loop 已运行）创建。

#### 39.3 per-target breaker / bulkhead

```python
def _get_breaker(self, target: str) -> CircuitBreaker:
    if target not in self._breakers:
        self._breakers[target] = CircuitBreaker(
            name=f"{self._service_name}->{target}",
            failure_threshold=self._cb_failure_threshold,
            cooldown_seconds=self._cb_cooldown_seconds,
        )
    return self._breakers[target]

def _get_bulkhead(self, target: str) -> Bulkhead:
    if target not in self._bulkheads:
        self._bulkheads[target] = Bulkhead(max_concurrent=self._bulkhead_concurrency)
    return self._bulkheads[target]
```

#### 39.4 方法签名 + retry 动态应用

```python
async def post(self, url: str, *, json_data: dict | None = None,
               target: str = "default", tenant_id: str | None = None) -> dict:
    breaker = self._get_breaker(target)
    bulkhead = self._get_bulkhead(target)

    async def _do():
        resp = await self._ensure_client().post(
            url, json=json_data, headers=self._headers(tenant_id)
        )
        return self._handle_response(resp)

    retried = retry(max_attempts=self._max_retries, name=target)(_do)
    async with bulkhead:
        return await breaker.call(retried)
```

同理实现 `get` / `put` / `delete`。`_headers(tenant_id)` 接受可选 tenant_id 覆盖默认值。

#### 39.5 _handle_response 升级

```python
def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
    if response.is_success:
        try:
            return response.json()
        except Exception:
            return {}
    status = response.status_code
    # 5xx 或 429 → ServiceException（可重试）
    if status >= 500 or status == 429:
        raise ServiceException(
            message=f"Upstream {status}: {response.text[:200]}",
            data={"upstream_status": status},
        )
    # 4xx（非 429）→ 尝试解析 body 为 AppException（不可重试）
    try:
        body = response.json()
        raise AppException(
            code=body.get("code", status),
            message=body.get("message", "Upstream error"),
            http_status=status,
            data=body.get("data"),
        )
    except AppException:
        raise
    except Exception:
        raise AppException(
            code=status,
            message=f"Upstream {status}: {response.text[:200]}",
            http_status=status,
        )
```

#### 39.6 保留 InternalHTTPSyncClient 原样

追加模块 docstring 标注 deprecated，代码不变。

**验证**：
```bash
python -c "from common_sdk.http_client import InternalHTTPClient; c = InternalHTTPClient('test'); print(type(c))"
```

---

### Step 40：迁移 `pipeline_orchestrator/tasks.py` + 幂等

**修改文件**：
- `project/backend/pipeline_orchestrator/tasks.py`（重写）
- `project/backend/pipeline_orchestrator/routes.py`（幂等守卫）
- `tests/test_pipeline_orchestrator.py`（更新 mock 策略）

#### 40.1 重构 tasks.py 为单 asyncio.run()

**问题**：当前 `run_pipeline_task`（sync Celery task）内多次调用 `asyncio.run()`，每次创建新事件循环。如果用模块级 `InternalHTTPClient`（lazy httpx client），client 会绑定到第一个已关闭的 loop，后续调用崩溃。且 breaker 状态无法跨 stage 持久。

**方案**：将 pipeline 主体重构为单个 `asyncio.run(_run_pipeline_async(...))`，内部创建 `InternalHTTPClient` 实例，跨 stage 复用（连接池 + breaker 状态持久）。

```python
# tasks.py 结构
from common_sdk.http_client import InternalHTTPClient
from common_sdk.exceptions import ServiceException

def _set_status(task, task_id, **fields): ...  # 保持 sync（用 task.redis_client）

# MySQL helpers 改为 async（移除内部 asyncio.run）
async def _create_pipeline(product_id, tenant_id) -> int: ...
async def _update_pipeline(pipeline_id, **fields) -> None: ...
async def _get_product(product_id) -> dict: ...

@create_task("run_pipeline", queue="orchestrator_queue")
def run_pipeline_task(self, task_id, product_id, tenant_id="default", config=None):
    config = config or {}
    _set_status(self, task_id, status="running", progress_percent="5")
    return asyncio.run(_run_pipeline_async(self, task_id, product_id, tenant_id, config))

async def _run_pipeline_async(task, task_id, product_id, tenant_id, config):
    http = InternalHTTPClient(SERVICE_NAME, timeout=300.0)
    pipeline_id = None
    try:
        pipeline_id = await _create_pipeline(product_id, tenant_id)
        # ... 各 stage 调用 http.post(url, json_data=body, target=service, tenant_id=tenant_id)
        # ... asyncio.gather 并行 generation（breaker 状态跨调用持久）
        pipeline_runs_total.labels(status="success").inc()
        return result
    except Exception as e:
        logger.error("pipeline_failed", product_id=product_id, error=str(e))
        pipeline_runs_total.labels(status="failed").inc()
        if pipeline_id is not None:
            try: await _update_pipeline(pipeline_id, stage="failed", error_message=str(e)[:500])
            except Exception: pass
        _set_status(task, task_id, status="failed", error=str(e))
        raise
    finally:
        # 幂等 key 清理（sync redis，从 async 上下文中调用可接受）
        try: task.redis_client.delete(f"pipeline:active:{product_id}:{tenant_id}")
        except Exception: pass
        await http.close()
```

**删除**：`_http_post` / `_http_get` / `_get_headers`（3 个 helper）+ `import httpx` + `from common_sdk.auth import create_service_jwt`

#### 40.2 幂等守卫（routes.py）

```python
# routes.py
import redis.asyncio as aioredis
from common_sdk.exceptions import ValidationException
from .config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB

_redis: aioredis.Redis | None = None

async def _get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            password=REDIS_PASSWORD or None, db=REDIS_DB,
            decode_responses=True,
        )
    return _redis

@router.post("/pipelines")
async def create_pipeline(request: Request, body: CreatePipelineRequest):
    redis = await _get_redis()
    idempotency_key = f"pipeline:active:{body.product_id}:{body.tenant_id}"
    task_id = f"pipe_{uuid.uuid4().hex[:12]}"
    acquired = await redis.set(idempotency_key, task_id, nx=True, ex=3600)
    if not acquired:
        existing = await redis.get(idempotency_key)
        raise ValidationException(
            f"Pipeline already active for product {body.product_id}",
            data={"existing_task_id": existing},
        )
    app = get_celery_app()
    app.send_task("pipeline_orchestrator.tasks.run_pipeline_task",
                  args=[task_id, body.product_id, body.tenant_id, body.config or {}],
                  queue="orchestrator_queue")
    return {"task_id": task_id, "product_id": body.product_id, "status": "queued"}
```

**幂等保证**：
- routes.py `SET NX EX 3600` → 重复提交返回 422
- tasks.py finally `DEL` → 任务完成（成功/失败）后释放，允许重试
- TTL 1h 兜底（任务崩溃未执行 finally 时自动释放）

#### 40.3 更新 test_pipeline_orchestrator.py mock 策略

**旧 mock**：`patch("...tasks.httpx.AsyncClient", return_value=mock_client)` — mock httpx，返回 mock response（有 `.json()`/`.raise_for_status()`）

**新 mock**：`patch.object(tasks, "InternalHTTPClient", return_value=mock_http)` — mock 整个 client，`post()` 直接返回 dict

```python
async def _post(url, *, json_data=None, target="default", tenant_id=None):
    for key, val in route_map.items():
        if key in url:
            if isinstance(val, Exception): raise val
            return val  # 直接返回 dict（不再需要 _make_mock_resp）
    raise RuntimeError(f"unexpected url: {url}")

mock_http = MagicMock()
mock_http.post = AsyncMock(side_effect=_post)
mock_http.close = AsyncMock()

with patch.object(tasks, "InternalHTTPClient", return_value=mock_http), \
     patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
     patch.object(tasks, "get_mysql_client", return_value=mock_mysql):
    ...
```

**删除**：`_make_mock_resp` / `_make_url_routing_post` helper 函数。MySQL mock 的 `execute`/`fetchone` 已是 AsyncMock，与 async helper 兼容。

**验证**：`python -m pytest tests/test_pipeline_orchestrator.py -v`（6 测试全通过）

---

### Step 41：迁移 `mcp_gateway/tool_handlers.py`

**修改文件**：`project/backend/mcp_gateway/tool_handlers.py`

#### 41.1 模块级 _http client

```python
from common_sdk.http_client import InternalHTTPClient
from .config import SERVICE_ENDPOINTS

_http = InternalHTTPClient("mcp-gateway", timeout=60.0)
```

**理由**：mcp_gateway 在 FastAPI 单事件循环中运行（无 asyncio.run 边界），模块级 client 可安全复用连接池。

#### 41.2 保留 _post / _get 签名作为薄适配器

```python
async def _post(service: str, path: str, body: dict, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    return await _http.post(url, json_data=body, target=service, tenant_id=tenant_id)

async def _get(service: str, path: str, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    return await _http.get(url, target=service, tenant_id=tenant_id)
```

**删除**：`_get_jwt()` + `import httpx`。所有 `handle_*` 函数不变（它们调 `_post`/`_get`，签名兼容）。

**验证**：
```bash
python -c "from project.backend.mcp_gateway.tool_handlers import handle_crawl_hot_product; print('OK')"
python -m pytest tests/test_mcp_gateway.py -v
```

---

### Step 42：整合 AI 适配器熔断

**修改文件**：
- `utils/model_adapters/base.py`
- `utils/model_adapters/registry.py`

#### 42.1 BaseModelAdapter 委托 CircuitBreaker（保留兼容属性）

**探索发现**：
- `mark_failure`/`mark_success` 被 7 个具体 adapter 调用（llm_openai, llm_claude, image_dalle, image_comfyui, video_veo3, video_sora, tts_azure）
- `is_healthy`/`failure_count` 被测试直接赋值（`_adapter` helper: `a.is_healthy = healthy; a.failure_count = 0`）
- 因此这些属性必须保持可变实例属性，不能改为只读 property

**方案**：保留原有属性 + 逻辑，并行同步 breaker（breaker 用于 Step 43 指标 + 未来 HALF_OPEN 探测）：

```python
from common_sdk.resilience import CircuitBreaker, CircuitBreakerState

class BaseModelAdapter(ABC):
    _DEGRADATION_THRESHOLD = 3
    _COOLDOWN_SECONDS = 300

    def __init__(self, ...):
        ...
        self.is_healthy = True
        self.failure_count = 0
        self.disabled_until = None
        self._breaker = CircuitBreaker(
            name=adapter_id,
            failure_threshold=self._DEGRADATION_THRESHOLD,
            cooldown_seconds=self._COOLDOWN_SECONDS,
        )

    def mark_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self._DEGRADATION_THRESHOLD:
            self.is_healthy = False
            self.disabled_until = time.time() + self._COOLDOWN_SECONDS
        self._breaker.record_failure()  # 同步 breaker

    def mark_success(self) -> None:
        self.failure_count = 0
        self.is_healthy = True
        self.disabled_until = None
        self._breaker.record_success()  # 同步 breaker

    def can_accept(self) -> bool:
        # 保留原有逻辑（检查 is_healthy / disabled_until）
        if not self.is_healthy:
            return False
        if self.disabled_until is not None and time.time() < self.disabled_until:
            return False
        if self.disabled_until is not None and time.time() >= self.disabled_until:
            self.disabled_until = None
        return True
```

#### 42.2 ModelRouter 统一到 adapter（消除双重计数）

**探索发现**：`track_failure` 在 `ai_generation/routes.py` 有 4 处调用（非死代码），`track_success` 无调用方，`get_status_hash` 无调用方。

```python
class ModelRouter:
    def __init__(self, registry):
        self._registry = registry
        # 删除 _failure_counts / _degraded

    def route(self, adapter_type, product_tier=None, preferred_model=None):
        candidates = self._registry.list_adapters(adapter_type)
        healthy = [a for a in candidates if a.can_accept()]
        # 删除 _degraded 检查（can_accept 已覆盖）
        ...

    def track_failure(self, adapter_id: str) -> None:
        adapter = self._registry.get_adapter(adapter_id)
        if adapter:
            adapter.mark_failure()  # 委托 adapter（统一到 breaker）

    def track_success(self, adapter_id: str) -> None:
        adapter = self._registry.get_adapter(adapter_id)
        if adapter:
            adapter.mark_success()

    def get_status_hash(self, adapter_id: str) -> dict[str, str]:
        adapter = self._registry.get_adapter(adapter_id)
        if not adapter:
            return {"failure_count": "0", "degraded_until": ""}
        return {
            "failure_count": str(adapter.failure_count),
            "degraded_until": str(adapter.disabled_until) if adapter.disabled_until else "",
        }
```

**行为变更**：`track_failure` 现在调 `adapter.mark_failure()`（原来自行维护 `_failure_counts`）。由于 adapter 内部失败时已调 `self.mark_failure()`，route 异常处理再调 `track_failure` → `adapter.mark_failure()` 会导致**单次失败计数 +2**（adapter 内部 1 次 + route handler 1 次）。这是**既有行为**（原双重计数：adapter `_failure_count` +1 + router `_failure_counts` +1），本次不修复（超出 Phase 8 范围），仅统一实现路径。

**验证**：`python -m pytest tests/test_model_adapters.py -v`（全部通过）

---

### Step 43：韧性 Prometheus 指标 + 文档

**修改文件**：
- `utils/common_sdk/resilience.py`（追加指标埋点）
- `doc/observability.md`（追加韧性章节）
- `README.md`（测试数量更新）

#### 43.1 指标定义（resilience.py 内，自包含无循环依赖）

```python
from prometheus_client import Counter, Gauge

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["name"],
)
circuit_breaker_rejected_total = Counter(
    "circuit_breaker_rejected_total",
    "Total calls rejected by open circuit breakers",
    ["name"],
)
retry_attempts_total = Counter(
    "retry_attempts_total",
    "Total retry attempts",
    ["name"],
)
```

#### 43.2 埋点

- `CircuitBreaker._record_failure_locked` / `_record_success_locked` / `_compute_state_locked`：状态变更时 `circuit_breaker_state.labels(name=...).set(int(state))`
- `CircuitBreaker.call` 拒绝时：`circuit_breaker_rejected_total.labels(name=...).inc()`
- `retry` 装饰器 `_before_sleep`：`retry_attempts_total.labels(name=...).inc()`

#### 43.3 文档

`doc/observability.md` 追加"韧性模式（Phase 8）"章节：熔断/重试/限流/舱壁的配置项、指标说明、Grafana 面板建议。

**验证**：`grep "circuit_breaker_state" utils/common_sdk/resilience.py`

---

### Step 44：Phase 8 测试 + 全量回归

**新建文件**：
- `tests/test_phase8_resilience.py`
- `tests/test_phase8_http_client.py`
- `tests/test_phase8_pipeline_idempotency.py`

#### test_phase8_resilience.py（~8 测试）
1. `test_circuit_breaker_starts_closed` — 新建 breaker.state == CLOSED
2. `test_circuit_breaker_opens_after_threshold` — N 次失败后 state == OPEN
3. `test_circuit_breaker_rejects_when_open` — OPEN 时 call() raises CircuitBreakerOpenError
4. `test_circuit_breaker_half_open_after_cooldown` — cooldown 后 state == HALF_OPEN
5. `test_circuit_breaker_closes_on_half_open_success` — HALF_OPEN 探测成功 → CLOSED
6. `test_retry_retries_on_service_exception` — tenacity 重试 ServiceException（max_attempts 次）
7. `test_retry_does_not_retry_on_circuit_open` — CircuitBreakerOpenError 不重试
8. `test_bulkhead_limits_concurrency` — 并发数不超 max_concurrent

#### test_phase8_http_client.py（~5 测试，用 httpx.MockTransport）
1. `test_handle_response_5xx_raises_service_exception` — 500 → ServiceException（可重试）
2. `test_handle_response_4xx_raises_app_exception` — 404 → AppException（不可重试）
3. `test_handle_response_429_raises_service_exception` — 429 → ServiceException（可重试）
4. `test_per_target_breaker_isolation` — target A 熔断不影响 target B
5. `test_close_releases_client` — close() 后 _client 为 None

#### test_phase8_pipeline_idempotency.py（~2 测试）
1. `test_duplicate_pipeline_request_rejected` — 第二次 SET NX 返回 None → ValidationException 422
2. `test_idempotency_key_deleted_on_task_completion` — mock task 完成后 redis.delete 被调用

**更新文件**：
- `tests/test_pipeline_orchestrator.py`（mock 策略迁移，见 Step 40.3）

**全量回归**：`python -m pytest tests/ -v --tb=short`，预期 ~150+ passed

---

## 关键文件清单

| 文件 | 操作 | Step |
|------|------|------|
| `utils/common_sdk/resilience.py` | 已创建 + 追加指标 | 38 ✅ / 43 |
| `utils/common_sdk/__init__.py` | 已修改（导出） | 38 ✅ |
| `utils/common_sdk/http_client.py` | **重写** InternalHTTPClient | 39 |
| `project/backend/pipeline_orchestrator/tasks.py` | **重写**（单 asyncio.run + 迁移 + 幂等清理） | 40 |
| `project/backend/pipeline_orchestrator/routes.py` | 修改（幂等守卫） | 40 |
| `project/backend/mcp_gateway/tool_handlers.py` | 修改（迁移） | 41 |
| `utils/model_adapters/base.py` | 修改（委托 breaker） | 42 |
| `utils/model_adapters/registry.py` | 修改（统一 track_failure） | 42 |
| `doc/observability.md` | 修改（韧性章节） | 43 |
| `README.md` | 修改（测试数量） | 44 |
| `tests/test_phase8_resilience.py` | **新建** | 44 |
| `tests/test_phase8_http_client.py` | **新建** | 44 |
| `tests/test_phase8_pipeline_idempotency.py` | **新建** | 44 |
| `tests/test_pipeline_orchestrator.py` | 修改（mock 策略） | 40 |

## 验证

1. **单元测试**：`python -m pytest tests/test_phase8_*.py -v`
2. **全量回归**：`python -m pytest tests/ -v --tb=short`，预期 ~150+ passed
3. **import 检查**：pipeline_orchestrator / mcp_gateway main.py import 成功
4. **现有测试不破**：`test_pipeline_orchestrator.py`（6）、`test_model_adapters.py`（adapter 熔断）、`test_mcp_gateway.py`（MCP 工具）全部通过
