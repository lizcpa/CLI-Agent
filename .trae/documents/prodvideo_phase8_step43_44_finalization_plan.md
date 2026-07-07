# Phase 8 收尾：Step 43（文档）+ Step 44（测试 + 全量回归）

## Context

Phase 8（韧性模式）的代码实现已全部完成（Step 38-42 + Step 43 指标埋点）。本计划覆盖**仅剩的收尾工作**：

- **Step 43 剩余**：`doc/observability.md` 追加韧性章节 + `README.md` 更新
- **Step 44 完整**：创建 3 个 Phase 8 测试文件（15 测试）+ 全量回归

代码探索已确认以下均已就绪：
- `utils/common_sdk/resilience.py` — 3 个 Prometheus 指标已埋点（`circuit_breaker_state` / `circuit_breaker_rejected_total` / `retry_attempts_total`），所有状态转换方法已调用 `_record_breaker_state`
- `utils/common_sdk/http_client.py` — `InternalHTTPClient` 已重写（lazy httpx + per-target breaker/bulkhead + retry + `_handle_response` 升级）
- `project/backend/pipeline_orchestrator/tasks.py` — 单 `asyncio.run()` 重构 + 幂等 key 清理（finally 块 L238-243）
- `project/backend/pipeline_orchestrator/routes.py` — 幂等守卫 `SET NX EX 3600`（L44-56）
- `utils/model_adapters/base.py` — `BaseModelAdapter` 委托 `_breaker.record_failure/success`
- `tests/test_pipeline_orchestrator.py` — mock 策略已迁移（`_make_mock_http` + `patch.object(tasks, "InternalHTTPClient")`）

---

## Step 43 剩余：文档更新

### 43.1 修改 `doc/observability.md`

**位置**：在 `## 告警规则` 章节（L110-120）之后、`## 健康检查`（L122）之前，插入新的顶级章节 `## 韧性模式（Phase 8）`。

**内容**：

```markdown
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
```

### 43.2 修改 `README.md`

**变更 1**：L17 `├── tests/                  # 单元测试（138+ 测试）` → `├── tests/                  # 单元测试（150+ 测试）`

**变更 2**：在 L98（技术栈 - 可观测性行）之后追加一行：
```
- **韧性**: tenacity (重试) + 自研 CircuitBreaker/Bulkhead/RateLimiter + httpx 连接池 + Redis 幂等
```

**验证**：`grep "韧性" README.md` + `grep "150+" README.md`

---

## Step 44：Phase 8 测试 + 全量回归

### 44.1 新建 `tests/test_phase8_resilience.py`（8 测试）

**测试 CircuitBreaker 状态机（5 测试）+ retry 谓词（2 测试）+ Bulkhead（1 测试）**

```python
"""Phase 8 tests: CircuitBreaker state machine, retry predicate, Bulkhead."""
from __future__ import annotations

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import pytest
from common_sdk.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    Bulkhead,
    retry,
)
from common_sdk.exceptions import ServiceException


# ── CircuitBreaker state machine ──────────────────────────────

def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=30)
    assert cb.state == CircuitBreakerState.CLOSED


async def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, cooldown_seconds=30)
    async def fail():
        raise RuntimeError("boom")
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(fail)
    assert cb.state == CircuitBreakerState.OPEN


async def test_circuit_breaker_rejects_when_open():
    cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=30)
    async def fail():
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await cb.call(fail)
    assert cb.state == CircuitBreakerState.OPEN
    async def ok():
        return "ok"
    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(ok)


async def test_circuit_breaker_half_open_after_cooldown():
    cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=0.1)
    async def fail():
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await cb.call(fail)
    await asyncio.sleep(0.15)
    assert cb.state == CircuitBreakerState.HALF_OPEN


async def test_circuit_breaker_closes_on_half_open_success():
    cb = CircuitBreaker("test", failure_threshold=1, cooldown_seconds=0.1)
    async def fail():
        raise RuntimeError("boom")
    async def ok():
        return "ok"
    with pytest.raises(RuntimeError):
        await cb.call(fail)
    await asyncio.sleep(0.15)
    result = await cb.call(ok)
    assert result == "ok"
    assert cb.state == CircuitBreakerState.CLOSED


# ── retry predicate ───────────────────────────────────────────

async def test_retry_retries_on_service_exception():
    calls = 0
    @retry(max_attempts=3, initial_backoff=0.01, name="test")
    async def flaky():
        nonlocal calls
        calls += 1
        raise ServiceException("transient")
    with pytest.raises(ServiceException):
        await flaky()
    assert calls == 3


async def test_retry_does_not_retry_on_circuit_open():
    calls = 0
    @retry(max_attempts=3, initial_backoff=0.01, name="test")
    async def fail():
        nonlocal calls
        calls += 1
        raise CircuitBreakerOpenError("test")
    with pytest.raises(CircuitBreakerOpenError):
        await fail()
    assert calls == 1


# ── Bulkhead ──────────────────────────────────────────────────

async def test_bulkhead_limits_concurrency():
    bh = Bulkhead(max_concurrent=2)
    in_flight = 0
    max_seen = 0
    async def task():
        nonlocal in_flight, max_seen
        async with bh:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
    await asyncio.gather(*[task() for _ in range(6)])
    assert max_seen <= 2
```

**关键设计点**：
- `pytest.ini` 设 `asyncio_mode = auto`，async 测试无需 `@pytest.mark.asyncio` 装饰器
- `initial_backoff=0.01` 避免测试因 backoff 慢
- `cooldown_seconds=0.1` + `sleep(0.15)` 测试 HALF_OPEN 转换
- `CircuitBreakerOpenError` 是 `ServiceException` 子类，但 `_should_retry` 先检查 `isinstance(exc, CircuitBreakerOpenError)` 返回 False → 不重试

### 44.2 新建 `tests/test_phase8_http_client.py`（5 测试）

**用 `httpx.MockTransport` 注入响应，测试 `InternalHTTPClient` 端到端行为**

```python
"""Phase 8 tests: InternalHTTPClient response handling + per-target breaker isolation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))

import httpx
import pytest

from common_sdk.http_client import InternalHTTPClient
from common_sdk.exceptions import AppException, ServiceException


def _make_client(transport: httpx.MockTransport, **kwargs) -> InternalHTTPClient:
    """Build an InternalHTTPClient with a pre-injected mock transport.

    Bypasses lazy init by setting _client directly, so no real network is used.
    """
    client = InternalHTTPClient("test-svc", timeout=5.0, max_retries=1, **kwargs)
    client._client = httpx.AsyncClient(transport=transport)
    return client


async def test_handle_response_5xx_raises_service_exception():
    def handler(req):
        return httpx.Response(500, text="Internal Server Error")
    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(ServiceException):
            await client.post("http://test/api", target="t1")
    finally:
        await client.close()


async def test_handle_response_4xx_raises_app_exception():
    def handler(req):
        return httpx.Response(404, json={"code": 404, "message": "Not Found"})
    client = _make_client(httpx.MockTransport(handler))
    try:
        with pytest.raises(AppException) as exc_info:
            await client.get("http://test/api", target="t1")
        assert exc_info.value.http_status == 404
    finally:
        await client.close()


async def test_handle_response_429_raises_service_exception():
    def handler(req):
        return httpx.Response(429, text="Too Many Requests")
    client = _make_client(httpx.MockTransport(handler), max_retries=1)
    try:
        with pytest.raises(ServiceException):
            await client.post("http://test/api", target="t1")
    finally:
        await client.close()


async def test_per_target_breaker_isolation():
    """Target A failing does not block target B (independent breakers)."""
    def handler(req):
        if "fail" in str(req.url):
            return httpx.Response(500, text="err")
        return httpx.Response(200, json={"ok": True})
    client = _make_client(
        httpx.MockTransport(handler),
        max_retries=1,
        cb_failure_threshold=1,
    )
    try:
        # Drive target A to open
        with pytest.raises(ServiceException):
            await client.post("http://test/fail", target="A")
        # Target B should still succeed
        result = await client.get("http://test/ok", target="B")
        assert result == {"ok": True}
    finally:
        await client.close()


async def test_close_releases_client():
    client = InternalHTTPClient("test-svc")
    client._client = httpx.AsyncClient()  # force lazy init
    await client.close()
    assert client._client is None
```

**关键设计点**：
- `httpx.MockTransport(handler)` — httpx 官方 mock 机制，无需第三方库
- `_make_client` 直接注入 `client._client`，绕过 lazy init（测试不依赖网络/环境变量）
- `max_retries=1` 避免重试拖慢测试（5xx 测试只调用 1 次）
- `cb_failure_threshold=1` 使隔离测试中 target A 一次失败即熔断
- 隔离测试用 URL 路径区分 target A（`/fail`）和 target B（`/ok`）

### 44.3 新建 `tests/test_phase8_pipeline_idempotency.py`（2 测试）

**测试 routes.py 幂等守卫 + tasks.py finally 清理**

```python
"""Phase 8 tests: pipeline idempotency guard + cleanup."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from common_sdk.exceptions import ValidationException


async def test_duplicate_pipeline_request_rejected():
    """Second SET NX returns None → ValidationException raised."""
    from project.backend.pipeline_orchestrator.routes import (
        create_pipeline,
        CreatePipelineRequest,
    )

    mock_redis = MagicMock()
    # First call acquires (True), second call fails (None)
    mock_redis.set = AsyncMock(side_effect=[True, None])
    mock_redis.get = AsyncMock(return_value="existing_task_id")

    body = CreatePipelineRequest(product_id=100, tenant_id="default")
    req = MagicMock()

    with patch(
        "project.backend.pipeline_orchestrator.routes._get_redis",
        return_value=mock_redis,
    ), patch("mq_clients.celery_app.get_celery_app") as mock_celery:
        # First call succeeds
        result = await create_pipeline(req, body)
        assert result["status"] == "queued"

        # Second call raises ValidationException
        with pytest.raises(ValidationException) as exc_info:
            await create_pipeline(req, body)
        assert "already active" in exc_info.value.message
        assert exc_info.value.data["existing_task_id"] == "existing_task_id"


def test_idempotency_key_deleted_on_task_completion():
    """tasks.py finally block deletes pipeline:active:{product_id}:{tenant_id}."""
    from project.backend.pipeline_orchestrator import tasks
    from mq_clients.celery_app import BaseTask

    mock_redis = MagicMock()
    mock_mysql = MagicMock()
    mock_mysql.execute = AsyncMock()
    mock_mysql.fetchone = AsyncMock(side_effect=[
        {"id": 1},
        {"id": 100, "title": "Test", "description": "d", "main_image_url": "", "tags": []},
    ])

    route_map = {
        "analyze": {"analyzed_count": 1, "hot_count": 1},
        "copywriting": {"text": "文案"},
        "images/generate": {"image_objects": ["img1.jpg"]},
        "videos/generate": {"clip_objects": ["c1.mp4"]},
        "compose": {"output_object": "out.mp4"},
        "publish": {"publish_log_id": 42},
    }
    from tests.test_pipeline_orchestrator import _make_mock_http
    mock_http = _make_mock_http(route_map)

    with patch.object(BaseTask, "redis_client", new_callable=PropertyMock) as mock_rc, \
         patch.object(tasks, "get_mysql_client", return_value=mock_mysql), \
         patch.object(tasks, "InternalHTTPClient", return_value=mock_http):
        mock_rc.return_value = mock_redis
        tasks.run_pipeline_task.run(
            task_id="t1", product_id=100, tenant_id="default", config=None,
        )

    mock_redis.delete.assert_any_call("pipeline:active:100:default")
```

**关键设计点**：
- 测试 1 直接调用 `create_pipeline` async 函数（绕过 FastAPI HTTP 层，避免 lifespan/exception handler 依赖）
- 测试 2 复用 `tests.test_pipeline_orchestrator._make_mock_http` helper（DRY）
- 测试 2 验证 `mock_redis.delete` 被调用且参数为 `pipeline:active:100:default`（product_id:tenant_id 格式）

### 44.4 全量回归

```bash
python -m pytest tests/ -v --tb=short
```

**预期**：~153 passed（138 既有 + 15 新增）

**关键回归点**：
- `test_common_sdk.py`（24）— 确认 resilience 指标导入未破坏
- `test_pipeline_orchestrator.py`（6）— 确认 InternalHTTPClient mock 策略仍工作
- `test_mcp_gateway.py`（14）— 确认 mcp_gateway 迁移未破坏（注意 retry backoff 可能慢 ~97s）
- `test_model_adapters.py`（18）— 确认 breaker 集成未破坏

---

## 文件清单

| 文件 | 操作 | Step |
|------|------|------|
| `doc/observability.md` | 修改（追加韧性章节） | 43 |
| `README.md` | 修改（测试数 + 韧性行） | 43 |
| `tests/test_phase8_resilience.py` | **新建**（8 测试） | 44 |
| `tests/test_phase8_http_client.py` | **新建**（5 测试） | 44 |
| `tests/test_phase8_pipeline_idempotency.py` | **新建**（2 测试） | 44 |

## 验证

1. **文档**：`grep "韧性模式" doc/observability.md` + `grep "150+" README.md` + `grep "韧性" README.md`
2. **指标埋点**（Step 43 验证）：`grep "circuit_breaker_state" utils/common_sdk/resilience.py`
3. **Phase 8 测试**：`python -m pytest tests/test_phase8_*.py -v`（15 passed）
4. **全量回归**：`python -m pytest tests/ -v --tb=short`（~153 passed）
