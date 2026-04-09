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

            current = await redis.get(key)
            if current and int(current) >= self.limit:
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

            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window)
            await pipe.execute()
        except HTTPException:
            raise
        except Exception:  # noqa: S110
            pass  # Redis error — don't block the request


# Pre-configured instances for common use cases
rate_limit_import = RateLimiter(limit=5, window=60, action="import")
rate_limit_write = RateLimiter(limit=30, window=60, action="write")
