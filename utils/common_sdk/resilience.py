"""Resilience patterns: CircuitBreaker, retry, RateLimiter, Bulkhead.

CircuitBreaker — 3-state (closed/open/half-open), in-process, async-safe.
retry          — async decorator with exponential backoff + jitter (tenacity).
RateLimiter    — token bucket, async, in-process.
Bulkhead       — asyncio.Semaphore wrapper limiting concurrent calls.
"""
from __future__ import annotations

import asyncio
import functools
import time
from enum import IntEnum
from typing import Any, Callable

import httpx
from prometheus_client import REGISTRY, Counter, Gauge
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .exceptions import ServiceException
from .logger import get_logger

logger = get_logger(__name__)

# ─── Prometheus metrics ──────────────────────────────────────────


def _get_or_create(metric_cls, name: str, description: str, labels: list[str]):
    """Register a metric or return the existing one (idempotent).

    Guards against dual-import: ``common_sdk.resilience`` and
    ``utils.common_sdk.resilience`` are distinct module objects in Python,
    so the module body can execute twice in the same process. Without this
    guard, the second execution raises ValueError on duplicate registration.
    """
    try:
        return metric_cls(name, description, labels)
    except ValueError:
        key = name[:-6] if (metric_cls is Counter and name.endswith("_total")) else name
        return REGISTRY._names_to_collectors[key]


circuit_breaker_state = _get_or_create(
    Gauge,
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["name"],
)
circuit_breaker_rejected_total = _get_or_create(
    Counter,
    "circuit_breaker_rejected_total",
    "Total calls rejected by open circuit breakers",
    ["name"],
)
retry_attempts_total = _get_or_create(
    Counter,
    "retry_attempts_total",
    "Total retry attempts",
    ["name"],
)


def _record_breaker_state(name: str, state: CircuitBreakerState) -> None:
    circuit_breaker_state.labels(name=name).set(int(state))


# ─── CircuitBreaker ──────────────────────────────────────────────

class CircuitBreakerState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


class CircuitBreakerOpenError(ServiceException):
    """Raised when a call is rejected because the breaker is open."""

    def __init__(self, name: str) -> None:
        super().__init__(message=f"Circuit breaker '{name}' is open")


class CircuitBreaker:
    """3-state circuit breaker (closed → open → half-open → closed/open).

    All state transitions are async-safe via an ``asyncio.Lock``.  The
    breaker is in-process (not distributed) — each service instance
    tracks its own view of downstream health.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_max_calls = half_open_max_calls
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_calls = 0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitBreakerState:
        """Logical state (lazily reports HALF_OPEN when cooldown expires).

        Does NOT mutate ``_state`` — the actual transition happens inside
        ``call()`` under lock.  Safe to call from any coroutine.
        """
        if self._state == CircuitBreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self._cooldown_seconds:
                return CircuitBreakerState.HALF_OPEN
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute ``func`` through the breaker.

        Raises ``CircuitBreakerOpenError`` if the breaker is open or
        half-open probe slots are exhausted.
        """
        async with self._lock:
            current = self._compute_state_locked()
            if current == CircuitBreakerState.OPEN:
                logger.warning("circuit_breaker_rejected", name=self._name)
                circuit_breaker_rejected_total.labels(name=self._name).inc()
                raise CircuitBreakerOpenError(self._name)
            if current == CircuitBreakerState.HALF_OPEN:
                if self._half_open_calls >= self._half_open_max_calls:
                    circuit_breaker_rejected_total.labels(name=self._name).inc()
                    raise CircuitBreakerOpenError(self._name)
                self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
        except Exception:
            self._record_failure_locked()
            raise
        else:
            self._record_success_locked()
            return result

    def _compute_state_locked(self) -> CircuitBreakerState:
        """Transition OPEN → HALF_OPEN if cooldown expired. Caller holds lock."""
        if self._state == CircuitBreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self._cooldown_seconds:
                self._state = CircuitBreakerState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("circuit_breaker_half_open", name=self._name)
                _record_breaker_state(self._name, self._state)
        return self._state

    def _record_success_locked(self) -> None:
        self._failure_count = 0
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.CLOSED
            self._half_open_calls = 0
            logger.info("circuit_breaker_closed", name=self._name)
            _record_breaker_state(self._name, self._state)

    def _record_failure_locked(self) -> None:
        self._failure_count += 1
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()
            self._half_open_calls = 0
            logger.warning(
                "circuit_breaker_opened",
                name=self._name,
                failure_count=self._failure_count,
            )
            _record_breaker_state(self._name, self._state)
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit_breaker_opened",
                name=self._name,
                failure_count=self._failure_count,
            )
            _record_breaker_state(self._name, self._state)

    # ── sync API for manual recording (used by BaseModelAdapter) ──

    def record_success(self) -> None:
        self._failure_count = 0
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.CLOSED
            self._half_open_calls = 0
            _record_breaker_state(self._name, self._state)

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == CircuitBreakerState.HALF_OPEN:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()
            self._half_open_calls = 0
            _record_breaker_state(self._name, self._state)
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()
            _record_breaker_state(self._name, self._state)

    def can_accept(self) -> bool:
        return self.state != CircuitBreakerState.OPEN


# ─── retry decorator ─────────────────────────────────────────────

def _should_retry(exc: Exception) -> bool:
    """Retry on transient errors; never retry circuit-breaker-open or 4xx."""
    if isinstance(exc, CircuitBreakerOpenError):
        return False
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, ServiceException):
        return True
    return False


def retry(
    *,
    max_attempts: int = 3,
    initial_backoff: float = 0.5,
    max_backoff: float = 10.0,
    name: str = "default",
) -> Callable:
    """Async retry decorator with exponential backoff + jitter.

    Retries on ``ServiceException`` (covers 5xx + 429), ``httpx.ConnectError``,
    and ``httpx.TimeoutException``.  Does NOT retry on ``CircuitBreakerOpenError``
    or 4xx ``AppException`` (deterministic errors).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _logger = get_logger(__name__)

            def _before_sleep(retry_state: RetryCallState) -> None:
                exc = retry_state.outcome.exception()
                _logger.warning(
                    "retry_attempt",
                    name=name,
                    attempt=retry_state.attempt_number,
                    max_attempts=max_attempts,
                    error=str(exc),
                )
                retry_attempts_total.labels(name=name).inc()

            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential_jitter(initial=initial_backoff, max=max_backoff),
                retry=retry_if_exception(_should_retry),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with attempt:
                    return await func(*args, **kwargs)

        return wrapper

    return decorator


# ─── RateLimiter (token bucket) ──────────────────────────────────

class RateLimiter:
    """Async token-bucket rate limiter.

    ``rate`` = tokens/second, ``burst`` = max accumulated tokens.
    ``acquire(n)`` blocks until ``n`` tokens are available, then consumes them.
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                self._last_refill = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self._rate
                await asyncio.sleep(wait)

    async def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to consume ``tokens``. Returns True on success, False if insufficient."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


# ─── Bulkhead (concurrency limiter) ──────────────────────────────

class Bulkhead:
    """Async bulkhead limiting concurrent in-flight calls via a semaphore."""

    def __init__(self, max_concurrent: int = 10) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)
        self._active = 0

    async def __aenter__(self) -> Bulkhead:
        await self._sem.acquire()
        self._active += 1
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._active -= 1
        self._sem.release()

    @property
    def active(self) -> int:
        return self._active
