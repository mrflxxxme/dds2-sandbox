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
        mock_redis.get = AsyncMock(return_value="2")  # 2 out of 5
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            # Should not raise
            await limiter(request)

        # Verify counter was incremented
        mock_pipe.incr.assert_called_once()
        mock_pipe.expire.assert_called_once_with("rate_limit:test_under:127.0.0.1", 60)

    @pytest.mark.asyncio
    async def test_blocks_requests_over_limit(self):
        """Requests exceeding the limit should get 429."""
        limiter = RateLimiter(limit=5, window=60, action="test_over")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="5")  # At limit

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
        mock_redis.get = AsyncMock(return_value="10")

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
        mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

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
        mock_redis.get = AsyncMock(return_value=None)
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await limiter(request)

        # Key must use client.host (127.0.0.1), NOT spoofable x-real-ip header
        mock_pipe.incr.assert_called_once_with("rate_limit:test_ip:127.0.0.1")

    @pytest.mark.asyncio
    async def test_first_request_no_existing_counter(self):
        """First request (redis.get returns None) should be allowed."""
        limiter = RateLimiter(limit=5, window=60, action="test_first")
        request = _make_request()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_pipe = AsyncMock()
        mock_pipe.incr = MagicMock()
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await limiter(request)

        mock_pipe.incr.assert_called_once()

    @pytest.mark.asyncio
    async def test_different_actions_independent(self):
        """Different action names should use separate rate limit counters."""
        limiter_a = RateLimiter(limit=5, window=60, action="action_a")
        limiter_b = RateLimiter(limit=5, window=60, action="action_b")
        request = _make_request()

        calls = []

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_pipe = AsyncMock()

        def track_incr(key):
            calls.append(key)

        mock_pipe.incr = MagicMock(side_effect=track_incr)
        mock_pipe.expire = MagicMock()
        mock_pipe.execute = AsyncMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipe)

        with (
            patch("backend.utils.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await limiter_a(request)
            await limiter_b(request)

        assert "rate_limit:action_a:127.0.0.1" in calls
        assert "rate_limit:action_b:127.0.0.1" in calls

    @pytest.mark.asyncio
    async def test_pre_configured_instances(self):
        """Verify pre-configured instances have correct settings."""
        from backend.utils.rate_limit import rate_limit_import, rate_limit_write

        assert rate_limit_import.limit == 5
        assert rate_limit_import.window == 60
        assert rate_limit_import.action == "import"

        assert rate_limit_write.limit == 30
        assert rate_limit_write.window == 60
        assert rate_limit_write.action == "write"
