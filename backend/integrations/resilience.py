"""
Resilience utilities for external API integrations.

Provides:
- CircuitBreaker: prevents cascading failures when external APIs are down
- retry_with_backoff: decorator for automatic retry with exponential backoff

Used by WB API client and other external integrations.
"""

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Optional

import structlog

logger = structlog.get_logger("dds.resilience")


# ─── Circuit Breaker ────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation — requests pass through
    OPEN = "open"            # Failures exceeded threshold — requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered — one request allowed


class CircuitBreaker:
    """
    Circuit breaker pattern for external API calls.

    States:
        CLOSED   → Normal. Counts consecutive failures.
        OPEN     → Blocked. All calls raise CircuitOpenError immediately.
        HALF_OPEN → One test call allowed. Success → CLOSED, Failure → OPEN.

    Usage:
        breaker = CircuitBreaker(name="wb_api", failure_threshold=5)

        async with breaker:
            result = await some_api_call()
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,  # seconds before trying again
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # Check if recovery period has elapsed
            if self._last_failure_time and (
                time.monotonic() - self._last_failure_time >= self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(
                    "circuit_breaker.half_open",
                    name=self.name,
                    recovery_timeout=self.recovery_timeout,
                )
        return self._state

    def record_success(self):
        """Record a successful call. Resets failure counter."""
        if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("circuit_breaker.closed", name=self.name)
            self._state = CircuitState.CLOSED

    def record_failure(self):
        """Record a failed call. May trip the breaker to OPEN."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker.open",
                name=self.name,
                reason="half_open_failure",
            )
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker.open",
                name=self.name,
                failures=self._failure_count,
                threshold=self.failure_threshold,
            )

    async def __aenter__(self):
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(
                f"Circuit breaker '{self.name}' is OPEN. "
                f"Retry after {self.recovery_timeout}s."
            )
        if state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
            if self._half_open_calls > self.half_open_max_calls:
                raise CircuitOpenError(
                    f"Circuit breaker '{self.name}' is HALF_OPEN, max test calls reached."
                )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()
        return False  # Don't suppress exceptions


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and request is blocked."""
    pass


# ─── Retry with Exponential Backoff ─────────────────────────────────────────

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple = (Exception,),
    retry_on_status: tuple = (429, 500, 502, 503, 504),
):
    """
    Async decorator: retry with exponential backoff.

    - Retries on specified exceptions
    - Special handling for HTTP 429 (rate limit): reads Retry-After header
    - Exponential backoff: base_delay * 2^attempt (capped at max_delay)

    Usage:
        @retry_with_backoff(max_retries=3, base_delay=2.0)
        async def fetch_data():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # Check if this is a retryable error
                    is_retryable = isinstance(e, retry_on)

                    # For ValueError with HTTP status codes
                    if isinstance(e, ValueError):
                        error_msg = str(e)
                        is_retryable = any(
                            f"({code})" in error_msg or f"HTTP {code}" in error_msg
                            for code in retry_on_status
                        )

                    if not is_retryable or attempt == max_retries:
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2 ** attempt), max_delay)

                    logger.warning(
                        "api.retry",
                        function=func.__name__,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay=delay,
                        error=str(e)[:200],
                    )

                    await asyncio.sleep(delay)

            raise last_exception  # Should not reach here, but safety net
        return wrapper
    return decorator
