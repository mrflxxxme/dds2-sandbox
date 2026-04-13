"""
Global API rate limiting middleware using Redis (pure ASGI — no BaseHTTPMiddleware).

Limits:
- Default: 300 requests per minute per IP
- Sync endpoints (/sync): 10 requests per minute per IP
- Import endpoints (/import): 20 requests per minute per IP

Counter lifecycle:
- INCR and EXPIRE are applied atomically via a Lua script.
- EXPIRE is only set on the FIRST request of a window (when INCR returns 1),
  so the TTL is not reset on every request. Otherwise a client making requests
  more frequently than `window` seconds would cause the counter to live forever
  and grow unbounded (see incident 2026-04-09: Prometheus /metrics scrape every
  15s with window=60s accumulated ~10000 blocked requests over 43 hours and
  eventually wedged uvicorn).
"""

import json
import logging
import os

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("dds.ratelimit")

# Rate limit configs: (max_requests, window_seconds)
RATE_LIMITS = {
    "default": (300, 60),
    "sync": (10, 60),
    "import": (20, 60),
}

# Paths that bypass rate limiting entirely.
# /metrics is scraped by Prometheus every 15s — it must never be rate-limited
# or throttled, or monitoring breaks AND the counter grows unbounded in Redis.
_SKIP_PATHS = {"/health", "/docs", "/openapi.json", "/metrics"}
_SKIP_SUFFIXES = ("_progress",)

# Atomic INCR + conditional EXPIRE.
# EXPIRE only runs when INCR returned 1 (i.e. key was just created),
# so the TTL is not reset on every request.
_INCR_EXPIRE_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def _get_limit_key(path: str) -> str:
    # Progress/status endpoints are read-only polling — use default limit
    if path.endswith(_SKIP_SUFFIXES):
        return "default"
    if "/sync" in path:
        return "sync"
    if "/import" in path:
        return "import"
    return "default"


class RateLimitMiddleware:
    """Redis-based rate limiting (pure ASGI). Fails open if Redis is unavailable."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Skip for tests, health, docs, OPTIONS
        if os.environ.get("TESTING") == "1":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path in _SKIP_PATHS or method == "OPTIONS":
            return await self.app(scope, receive, send)

        # Try X-Real-IP first (behind reverse proxy), fallback to direct client IP
        x_real_ip = None
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"x-real-ip":
                x_real_ip = header_value.decode()
                break
        if x_real_ip:
            client_ip = x_real_ip
        else:
            client = scope.get("client")
            client_ip = client[0] if client else "unknown"
        bucket = _get_limit_key(path)
        max_requests, window = RATE_LIMITS[bucket]

        try:
            from backend.cache import get_redis

            redis = await get_redis()
            if redis is not None:
                key = f"rl:{bucket}:{client_ip}"
                # Atomic: INCR + EXPIRE only on first request of the window.
                current = await redis.eval(_INCR_EXPIRE_LUA, 1, key, window)

                if current > max_requests:
                    logger.warning(
                        "Rate limit exceeded: %s on %s (%d/%d)",
                        client_ip,
                        bucket,
                        current,
                        max_requests,
                    )
                    body = json.dumps(
                        {
                            "detail": "Слишком много запросов. Подождите минуту.",
                            "retry_after": window,
                        }
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 429,
                            "headers": [
                                [b"content-type", b"application/json"],
                                [b"retry-after", str(window).encode()],
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
        except Exception as e:
            logger.debug("Rate limit check skipped (Redis): %s", e)

        await self.app(scope, receive, send)
