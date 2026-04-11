"""
Reusable rate limiter dependency for FastAPI endpoints.

Uses Redis with sliding window counter (same approach as auth.py).
Graceful degradation: if Redis unavailable, rate limiting is skipped.

Usage:
    from backend.utils.rate_limit import RateLimiter

    @router.post("/items", dependencies=[Depends(RateLimiter(limit=30, window=60))])
    async def create_item(...):
        ...
"""

import logging
import os

from fastapi import HTTPException, Request, status

logger = logging.getLogger("dds.rate_limit")

# Atomic INCR + conditional EXPIRE.
# EXPIRE only runs when INCR returned 1 (i.e. key was just created), so the TTL
# is not reset on every request. Without this, clients that make requests more
# frequently than `window` seconds would cause the counter to live forever and
# grow unbounded (see incident 2026-04-09).
_INCR_EXPIRE_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter:
    """
    FastAPI dependency that enforces per-IP rate limiting via Redis.

    Args:
        limit: Maximum number of requests allowed within the window.
        window: Time window in seconds (default: 60).
        action: Key prefix for distinguishing different rate limit buckets.
    """

    def __init__(self, limit: int = 30, window: int = 60, action: str = "api"):
        self.limit = limit
        self.window = window
        self.action = action

    async def __call__(self, request: Request) -> None:
        if os.environ.get("TESTING"):
            return  # Skip rate limiting in tests

        try:
            from backend.cache import get_redis

            redis = await get_redis()
            if redis is None:
                return  # Redis unavailable — skip rate limiting

            # Use X-Forwarded-For when behind nginx/proxy, fall back to direct IP
            forwarded = request.headers.get("X-Forwarded-For", "")
            client_ip = (
                forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
            )
            key = f"rate_limit:{self.action}:{client_ip}"

            # Atomic INCR + set TTL only on first request of the window.
            # Returns the post-increment value.
            current = await redis.eval(_INCR_EXPIRE_LUA, 1, key, self.window)

            if current > self.limit:
                logger.warning(
                    "Rate limit exceeded for %s from %s (%d/%d)",
                    self.action,
                    client_ip,
                    int(current),
                    self.limit,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Слишком много запросов. Подождите.",
                    headers={"Retry-After": str(self.window)},
                )
        except HTTPException:
            raise
        except Exception:  # noqa: S110
            pass  # Redis error — don't block the request


# Pre-configured instances for common use cases
rate_limit_import = RateLimiter(limit=5, window=60, action="import")
rate_limit_write = RateLimiter(limit=30, window=60, action="write")
# For heavy read endpoints whose cache-miss path runs expensive JOIN queries
# (e.g. supplier catalog) — protects against flood when Redis is unavailable.
rate_limit_read_heavy = RateLimiter(limit=60, window=60, action="read_heavy")
