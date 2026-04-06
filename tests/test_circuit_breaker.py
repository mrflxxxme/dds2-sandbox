"""
Tests for CircuitBreaker and CircuitBreakerRegistry.

backend/integrations/resilience.py — pure Python, no mocking needed
except for time.monotonic when testing recovery timeout transitions.
"""

import time
from unittest.mock import patch

import pytest

from backend.integrations.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
    RateLimitError,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CircuitBreakerRegistry
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerRegistry:
    """Tests for per-key circuit breaker registry."""

    def _make_registry(self, **kwargs) -> CircuitBreakerRegistry:
        defaults = dict(
            name_prefix="test",
            failure_threshold=5,
            recovery_timeout=60.0,
            exclude_errors=(RateLimitError,),
        )
        defaults.update(kwargs)
        return CircuitBreakerRegistry(**defaults)

    def test_registry_creates_per_key_breakers(self):
        """registry.get(1) and registry.get(2) return different instances."""
        reg = self._make_registry()
        b1 = reg.get(1)
        b2 = reg.get(2)
        assert b1 is not b2
        assert b1.name == "test-1"
        assert b2.name == "test-2"

    def test_registry_reuses_breaker_for_same_key(self):
        """registry.get(1) called twice returns the same instance."""
        reg = self._make_registry()
        first = reg.get(1)
        second = reg.get(1)
        assert first is second

    def test_registry_none_key_returns_fallback(self):
        """registry.get(None) returns the global fallback breaker."""
        reg = self._make_registry()
        fb = reg.get(None)
        assert fb is reg._fallback
        assert fb.name == "test-global"

    @pytest.mark.asyncio
    async def test_project_isolation(self):
        """Tripping the breaker for project 1 does not affect project 2."""
        reg = self._make_registry(failure_threshold=3, recovery_timeout=60.0)
        b1 = reg.get(1)
        b2 = reg.get(2)

        # Trip project 1's breaker with 3 failures (real exceptions)
        for _ in range(3):
            with pytest.raises(ValueError):
                async with b1:
                    raise ValueError("fail")

        assert b1.state == CircuitState.OPEN

        # Project 2 must still be CLOSED and usable
        assert b2.state == CircuitState.CLOSED
        async with b2:
            pass  # should not raise

    def test_registry_reset(self):
        """After reset(key), get(key) creates a fresh breaker."""
        reg = self._make_registry()
        original = reg.get(1)
        original._failure_count = 99  # pollute the instance

        reg.reset(1)

        fresh = reg.get(1)
        assert fresh is not original
        assert fresh._failure_count == 0

    def test_registry_reset_nonexistent_key(self):
        """reset() on a key that was never created is a no-op."""
        reg = self._make_registry()
        reg.reset(999)  # should not raise

    def test_active_count(self):
        """active_count tracks per-project breakers (not fallback)."""
        reg = self._make_registry()
        assert reg.active_count == 0

        reg.get(1)
        assert reg.active_count == 1

        reg.get(2)
        assert reg.active_count == 2

        # Accessing fallback does not affect count
        reg.get(None)
        assert reg.active_count == 2

        reg.reset(1)
        assert reg.active_count == 1

    def test_registry_passes_config_to_breakers(self):
        """Breakers created by the registry inherit its configuration."""
        reg = self._make_registry(
            failure_threshold=10,
            recovery_timeout=120.0,
            half_open_max_calls=3,
            exclude_errors=(RateLimitError, ValueError),
        )
        b = reg.get(42)
        assert b.failure_threshold == 10
        assert b.recovery_timeout == 120.0
        assert b.half_open_max_calls == 3
        assert b.exclude_errors == (RateLimitError, ValueError)


# ═══════════════════════════════════════════════════════════════════════════════
# CircuitBreaker — state machine
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerStateMachine:
    """Tests for the CircuitBreaker state transitions."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_basic_flow(self):
        """CLOSED -> 5 failures -> OPEN -> wait -> HALF_OPEN -> success -> CLOSED."""
        cb = CircuitBreaker(
            name="flow-test",
            failure_threshold=5,
            recovery_timeout=10.0,
        )
        assert cb.state == CircuitState.CLOSED

        # Phase 1: 5 failures trip the breaker to OPEN
        for _ in range(5):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("fail")

        assert cb.state == CircuitState.OPEN

        # Phase 2: while OPEN, calls are rejected
        with pytest.raises(CircuitOpenError):
            async with cb:
                pass

        # Phase 3: after recovery_timeout, state transitions to HALF_OPEN
        frozen_time = time.monotonic() + 11.0  # past the 10s timeout
        with patch("time.monotonic", return_value=frozen_time):
            assert cb.state == CircuitState.HALF_OPEN

        # Phase 4: a successful call in HALF_OPEN closes the breaker
        with patch("time.monotonic", return_value=frozen_time):
            async with cb:
                pass  # success (no exception)

        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        """A failure in HALF_OPEN immediately returns to OPEN."""
        cb = CircuitBreaker(
            name="reopen-test",
            failure_threshold=2,
            recovery_timeout=5.0,
        )

        # Trip to OPEN
        for _ in range(2):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("fail")
        assert cb.state == CircuitState.OPEN

        # Advance past recovery timeout to reach HALF_OPEN
        frozen_time = time.monotonic() + 6.0
        with patch("time.monotonic", return_value=frozen_time):
            assert cb.state == CircuitState.HALF_OPEN

            # Fail in HALF_OPEN
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("fail again")

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_exclude_errors(self):
        """Excluded errors do not increment failure count or trip the breaker."""
        cb = CircuitBreaker(
            name="exclude-test",
            failure_threshold=3,
            exclude_errors=(RateLimitError,),
        )

        # RateLimitError should not count as a failure
        for _ in range(10):
            with pytest.raises(RateLimitError):
                async with cb:
                    raise RateLimitError("rate limited", retry_after=1)

        # Breaker must still be CLOSED
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_non_excluded_errors_trip_breaker(self):
        """Regular exceptions DO trip the breaker."""
        cb = CircuitBreaker(
            name="trip-test",
            failure_threshold=3,
            exclude_errors=(RateLimitError,),
        )

        for _ in range(3):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("server error")

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        """A successful call resets the failure counter."""
        cb = CircuitBreaker(
            name="reset-test",
            failure_threshold=5,
        )

        # 4 failures (just below threshold)
        for _ in range(4):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("fail")

        assert cb._failure_count == 4
        assert cb.state == CircuitState.CLOSED

        # One success resets the counter
        async with cb:
            pass  # success

        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_max_calls(self):
        """In HALF_OPEN, only half_open_max_calls requests pass through."""
        cb = CircuitBreaker(
            name="half-open-limit",
            failure_threshold=2,
            recovery_timeout=5.0,
            half_open_max_calls=1,
        )

        # Trip to OPEN
        for _ in range(2):
            with pytest.raises(ValueError):
                async with cb:
                    raise ValueError("fail")

        frozen_time = time.monotonic() + 6.0
        with patch("time.monotonic", return_value=frozen_time), pytest.raises(ValueError):
            async with cb:
                raise ValueError("fail in half-open")

        assert cb.state == CircuitState.OPEN

        # Advance again past timeout
        frozen_time2 = frozen_time + 6.0
        with patch("time.monotonic", return_value=frozen_time2):
            assert cb.state == CircuitState.HALF_OPEN

            # First call allowed — consume the one allowed slot
            await cb.__aenter__()
            # Second call should be blocked
            with pytest.raises(CircuitOpenError, match="max test calls"):
                await cb.__aenter__()

    @pytest.mark.asyncio
    async def test_open_error_message(self):
        """CircuitOpenError contains the breaker name and timeout."""
        cb = CircuitBreaker(
            name="msg-test",
            failure_threshold=1,
            recovery_timeout=30.0,
        )

        with pytest.raises(ValueError):
            async with cb:
                raise ValueError("fail")

        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError, match="msg-test"):
            async with cb:
                pass

    def test_initial_state(self):
        """A new breaker starts in CLOSED state with zero failures."""
        cb = CircuitBreaker(name="init-test")
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0
        assert cb._last_failure_time is None

    @pytest.mark.asyncio
    async def test_exceptions_propagate(self):
        """The circuit breaker does not suppress exceptions (__aexit__ returns False)."""
        cb = CircuitBreaker(name="propagate-test", failure_threshold=10)

        with pytest.raises(RuntimeError, match="boom"):
            async with cb:
                raise RuntimeError("boom")

        # But the failure was still recorded
        assert cb._failure_count == 1
