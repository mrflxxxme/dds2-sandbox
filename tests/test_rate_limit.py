"""
Tests for backend/utils/rate_limit.py — reusable rate limiter dependency.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.utils.rate_limit import RateLimiter


def _make_request(ip: str = "127.0.0.1") -> MagicMock:
    """Create a mock FastAPI Request with the given client IP."""
    request = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = ip
    return request


def _make_request_with_header(ip: str = "10.0.0.1") -> MagicMock:
    """Create a mock request with x-real-ip header (reverse proxy)."""
    request = MagicMock()
    request.headers = {"x-real-ip": ip}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


class TestRateLimiterUnit:
    """Unit tests for RateLimiter (no Redis required)."""

    @pytest.mark.asyncio
    async def test_skip_in_testing_mode(self):
        """Rate limiter should be a no-op when TESTING env is set."""
        limiter = RateLimiter(limit=1, window=60, action="test")
        request = _make_request()

        # TESTING is already set by conftest_api.py
        with patch.dict(os.environ, {"TESTING": "1"}):
            # Should not raise even with limit=1
            await limiter(request)
            await limiter(request)
            await limiter(request)

    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self):
        """Requests under the limit should pass through."""
        limiter = RateLimiter(limit=5, window=60, action="test_under")
        request = _make_request()

        mock_redis = AsyncMock()
        # eval returns post-incr counter; 2 is under the limit of 5
        mock_redis.eval = AsyncMock(return_value=2)

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            # Should not raise
            await limiter(request)

        # Verify eval was called once with the expected key
        mock_redis.eval.assert_called_once()
        args = mock_redis.eval.call_args.args
        # eval(script, numkeys, key, window)
        assert args[1] == 1
        assert args[2] == "rate_limit:test_under:127.0.0.1"
        assert args[3] == 60

    @pytest.mark.asyncio
    async def test_blocks_requests_over_limit(self):
        """Requests exceeding the limit should get 429."""
        limiter = RateLimiter(limit=5, window=60, action="test_over")
        request = _make_request()

        mock_redis = AsyncMock()
        # Counter just tipped over the limit (6 > 5)
        mock_redis.eval = AsyncMock(return_value=6)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await limiter(request)

            assert exc_info.value.status_code == 429
            assert exc_info.value.headers["Retry-After"] == "60"

    @pytest.mark.asyncio
    async def test_blocks_with_custom_window(self):
        """Custom window should be reflected in Retry-After header."""
        limiter = RateLimiter(limit=10, window=120, action="test_window")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=11)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await limiter(request)

            assert exc_info.value.headers["Retry-After"] == "120"

    @pytest.mark.asyncio
    async def test_graceful_degradation_redis_unavailable(self):
        """When Redis returns None (unavailable), rate limiting should be skipped."""
        limiter = RateLimiter(limit=1, window=60, action="test_no_redis")
        request = _make_request()

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=None),
        ):
            # Should not raise even with limit=1
            await limiter(request)

    @pytest.mark.asyncio
    async def test_graceful_degradation_redis_error(self):
        """When Redis raises an exception, rate limiting should be skipped."""
        limiter = RateLimiter(limit=1, window=60, action="test_redis_err")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            # Should not raise despite Redis error
            await limiter(request)

    @pytest.mark.asyncio
    async def test_uses_client_host_not_header(self):
        """Rate limiter must use request.client.host, ignoring x-real-ip (prevents IP spoofing)."""
        limiter = RateLimiter(limit=5, window=60, action="test_ip")
        request = _make_request_with_header("10.0.0.1")

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await limiter(request)

        # Key must use client.host (127.0.0.1), NOT spoofable x-real-ip header
        mock_redis.eval.assert_called_once()
        args = mock_redis.eval.call_args.args
        assert args[2] == "rate_limit:test_ip:127.0.0.1"

    @pytest.mark.asyncio
    async def test_first_request_counted_as_one(self):
        """First request should be allowed (eval returns 1 = first in window)."""
        limiter = RateLimiter(limit=5, window=60, action="test_first")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            # Should not raise
            await limiter(request)

        mock_redis.eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_different_actions_independent(self):
        """Different action names should use separate rate limit counters."""
        limiter_a = RateLimiter(limit=5, window=60, action="action_a")
        limiter_b = RateLimiter(limit=5, window=60, action="action_b")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=1)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await limiter_a(request)
            await limiter_b(request)

        # Collect all eval key arguments (args[2] is the key)
        keys = [call.args[2] for call in mock_redis.eval.call_args_list]
        assert "rate_limit:action_a:127.0.0.1" in keys
        assert "rate_limit:action_b:127.0.0.1" in keys

    @pytest.mark.asyncio
    async def test_ttl_not_reset_on_subsequent_requests(self):
        """
        Regression test for incident 2026-04-09: the old implementation called
        `pipe.expire()` unconditionally on every request, which reset the TTL
        every time a client made a request faster than `window` seconds.

        The new Lua script only sets EXPIRE when INCR returns 1, so the TTL
        is not extended on later requests. This test documents that contract
        by checking the Lua script used in the eval call.
        """
        from backend.utils.rate_limit import _INCR_EXPIRE_LUA

        # The script MUST only call EXPIRE conditionally on `current == 1`.
        assert "INCR" in _INCR_EXPIRE_LUA
        assert "if current == 1" in _INCR_EXPIRE_LUA
        assert "EXPIRE" in _INCR_EXPIRE_LUA

    @pytest.mark.asyncio
    async def test_pre_configured_instances(self):
        """Verify pre-configured instances have correct settings."""
        from backend.utils.rate_limit import rate_limit_import, rate_limit_write

        assert rate_limit_import.limit == 5
        assert rate_limit_import.window == 60
        assert rate_limit_import.action == "import"

        assert rate_limit_write.limit == 60
        assert rate_limit_write.window == 60
        assert rate_limit_write.action == "write"
