"""
Global API rate limiting middleware using Redis.

Limits:
- Default: 100 requests per minute per IP
- Sync endpoints (/sync): 10 requests per minute per IP
- Import endpoints (/import): 20 requests per minute per IP
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("dds.ratelimit")

# Rate limit configs: (max_requests, window_seconds)
RATE_LIMITS = {
    "default": (300, 60),       # 300 req/min
    "sync": (10, 60),           # 10 req/min for sync endpoints
    "import": (20, 60),         # 20 req/min for import
}


def _get_limit_key(path: str) -> str:
    """Determine which rate limit bucket applies to a given path."""
    if "/sync" in path:
        return "sync"
    if "/import" in path:
        return "import"
    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-based rate limiting. Fails open (allows request) if Redis is unavailable."""

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health check, OPTIONS, and test mode
        import os
        if os.environ.get("TESTING") == "1":
            return await call_next(request)
        if request.url.path in ("/health", "/docs", "/openapi.json") or request.method == "OPTIONS":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        bucket = _get_limit_key(request.url.path)
        max_requests, window = RATE_LIMITS[bucket]

        try:
            from backend.cache import get_redis
            redis = await get_redis()
            if redis is None:
                return await call_next(request)

            key = f"rl:{bucket}:{client_ip}"

            # Atomic: increment first, then check result
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            results = await pipe.execute()
            current = results[0]  # INCR returns new value

            if current > max_requests:
                logger.warning(
                    f"Rate limit exceeded: {client_ip} on {bucket} "
                    f"({current}/{max_requests})"
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Слишком много запросов. Подождите минуту.",
                        "retry_after": window,
                    },
                    headers={"Retry-After": str(window)},
                )

        except Exception:
            pass  # Redis error — fail open, don't block requests

        return await call_next(request)
