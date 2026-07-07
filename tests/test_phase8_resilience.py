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

    async def ok():
        return "ok"

    with pytest.raises(RuntimeError):
        await cb.call(fail)
    assert cb.state == CircuitBreakerState.OPEN
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
