"""
Global API rate limiting middleware using Redis (pure ASGI — no BaseHTTPMiddleware).

Limits:
- Default: 300 requests per minute per IP
- Sync endpoints (/sync): 10 requests per minute per IP
- Import endpoints (/import): 20 requests per minute per IP
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

_SKIP_PATHS = {"/health", "/docs", "/openapi.json"}


def _get_limit_key(path: str) -> str:
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

        # Get client IP from scope
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        bucket = _get_limit_key(path)
        max_requests, window = RATE_LIMITS[bucket]

        try:
            from backend.cache import get_redis

            redis = await get_redis()
            if redis is not None:
                key = f"rl:{bucket}:{client_ip}"
                pipe = redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, window)
                results = await pipe.execute()
                current = results[0]

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
