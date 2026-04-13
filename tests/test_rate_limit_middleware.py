"""
Tests for backend/rate_limit.py — global ASGI rate limiting middleware.

Regression coverage for incident 2026-04-09:
1. /metrics must bypass rate limiting entirely (Prometheus scrape flood).
2. INCR+EXPIRE must be atomic with EXPIRE only applied on counter==1,
   so TTL doesn't get extended on every request.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from backend.rate_limit import (
    _INCR_EXPIRE_LUA,
    _SKIP_PATHS,
    RateLimitMiddleware,
    _get_limit_key,
)


class _DummyApp:
    """ASGI app stub that records whether it was called."""

    def __init__(self):
        self.called = False
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.scope = scope
        # Send a minimal 200 response so send stubs work
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _make_scope(path: str = "/api/items", method: str = "GET", client_ip: str = "10.0.0.3"):
    return {
        "type": "http",
        "path": path,
        "method": method,
        "headers": [],
        "client": (client_ip, 54321),
    }


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


class _SendRecorder:
    def __init__(self):
        self.messages: list = []

    async def __call__(self, message):
        self.messages.append(message)


class TestSkipPaths:
    """Paths that must never be rate-limited."""

    def test_metrics_in_skip_paths(self):
        """/metrics must be in _SKIP_PATHS (incident 2026-04-09)."""
        assert "/metrics" in _SKIP_PATHS

    def test_health_in_skip_paths(self):
        assert "/health" in _SKIP_PATHS

    def test_docs_in_skip_paths(self):
        assert "/docs" in _SKIP_PATHS
        assert "/openapi.json" in _SKIP_PATHS

    @pytest.mark.asyncio
    async def test_metrics_bypasses_middleware(self):
        """
        /metrics must bypass rate-limit entirely and never touch Redis.

        Prometheus scrapes /metrics every 15s — if the middleware counts it,
        Redis fills up with blocked counters and eventually wedges the app.
        """
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope(path="/metrics", client_ip="10.0.0.3")
        send = _SendRecorder()

        mock_redis = AsyncMock()
        # Force TESTING off so we exercise the real skip path
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        # App was called through (request was not blocked)
        assert app.called is True
        # Redis must NOT have been touched for /metrics
        mock_redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_bypasses_middleware(self):
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope(path="/health")
        send = _SendRecorder()

        mock_redis = AsyncMock()
        with (
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        assert app.called is True
        mock_redis.eval.assert_not_called()


class TestLuaScript:
    """The Lua script is the atomicity contract — it must be shaped correctly."""

    def test_lua_increments_and_conditionally_expires(self):
        """
        Regression for incident 2026-04-09: the pre-fix implementation called
        `pipe.expire(key, window)` on every request, which reset the TTL every
        time Prometheus scraped (15s < 60s window) and made the counter live
        forever.

        The new script MUST only call EXPIRE when INCR returned 1.
        """
        assert "INCR" in _INCR_EXPIRE_LUA
        assert "EXPIRE" in _INCR_EXPIRE_LUA
        assert "if current == 1" in _INCR_EXPIRE_LUA
        # The script must return the post-increment value so middleware can
        # compare against the limit.
        assert "return current" in _INCR_EXPIRE_LUA


class TestRateLimitBehavior:
    """Counter increment and 429 behavior."""

    @pytest.mark.asyncio
    async def test_request_under_limit_passes(self):
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope(path="/api/items")
        send = _SendRecorder()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=5)  # 5 < 300

        with (
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        assert app.called is True
        mock_redis.eval.assert_called_once()
        # eval(script, numkeys, key, window)
        args = mock_redis.eval.call_args.args
        assert args[0] == _INCR_EXPIRE_LUA
        assert args[1] == 1  # numkeys
        assert args[2].startswith("rl:default:")
        assert args[3] == 60  # default window

    @pytest.mark.asyncio
    async def test_request_over_limit_returns_429(self):
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope(path="/api/items")
        send = _SendRecorder()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(return_value=301)  # default limit is 300

        with (
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        # Underlying app must NOT be called — request was blocked.
        assert app.called is False
        # Response was sent with status 429
        start = next(m for m in send.messages if m["type"] == "http.response.start")
        assert start["status"] == 429
        body_msg = next(m for m in send.messages if m["type"] == "http.response.body")
        body = json.loads(body_msg["body"])
        assert "Слишком много запросов" in body["detail"]
        assert body["retry_after"] == 60

    @pytest.mark.asyncio
    async def test_testing_env_bypasses_entirely(self):
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope()
        send = _SendRecorder()

        mock_redis = AsyncMock()
        with (
            patch.dict(os.environ, {"TESTING": "1"}),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        assert app.called is True
        mock_redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_unavailable_fails_open(self):
        """If Redis returns None (down), requests should pass through."""
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope()
        send = _SendRecorder()

        with (
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=None),
        ):
            await middleware(scope, _noop_receive, send)

        assert app.called is True

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self):
        """If Redis raises, the middleware must not block the request."""
        app = _DummyApp()
        middleware = RateLimitMiddleware(app)
        scope = _make_scope()
        send = _SendRecorder()

        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))

        with (
            patch("backend.rate_limit.os.environ.get", return_value=None),
            patch("backend.cache.get_redis", return_value=mock_redis),
        ):
            await middleware(scope, _noop_receive, send)

        # App still called — fail-open.
        assert app.called is True


class TestBucketSelection:
    """_get_limit_key routing."""

    def test_default_bucket_for_regular_paths(self):
        assert _get_limit_key("/api/items") == "default"

    def test_sync_bucket(self):
        assert _get_limit_key("/api/wb/sync") == "sync"

    def test_import_bucket(self):
        assert _get_limit_key("/api/transactions/import") == "import"

    def test_progress_suffix_uses_default(self):
        """_progress polling endpoints share the default bucket."""
        assert _get_limit_key("/api/wb/sync_progress") == "default"
