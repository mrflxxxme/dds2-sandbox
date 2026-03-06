"""
Redis cache utilities for DDS reports and expensive queries.
"""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from typing import Optional

import redis.asyncio as aioredis

from backend.config import settings

logger = logging.getLogger(__name__)

# Lazy Redis connection
_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Get or create Redis connection."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            await _redis_client.ping()
            logger.info("Redis connected: %s", settings.REDIS_URL)
        except Exception as e:
            logger.warning("Redis unavailable (%s), caching disabled", e)
            _redis_client = None
    return _redis_client


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def cached(prefix: str, ttl: int = 300):
    """
    Cache decorator for async API endpoints.

    Args:
        prefix: Cache key prefix (e.g. "reports:balance")
        ttl: Time-to-live in seconds (default 5 min)
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            r = await get_redis()
            if r is None:
                # Redis unavailable — skip cache, run function directly
                return await func(*args, **kwargs)

            # Build cache key from prefix + function args
            # Ensure project_id is ALWAYS part of the key for multi-tenant safety
            key_parts = [prefix]
            for k, v in sorted(kwargs.items()):
                if k == "db":
                    continue  # skip database session
                if k == "project" and hasattr(v, "id"):
                    key_parts.append(f"pid={v.id}")  # extract ID from Project object
                elif v is not None:
                    key_parts.append(f"{k}={v}")
            cache_key = ":".join(key_parts)

            try:
                # Try cache hit
                cached_value = await r.get(cache_key)
                if cached_value is not None:
                    logger.debug("Cache HIT: %s", cache_key)
                    return json.loads(cached_value)
            except Exception as e:
                logger.warning("Cache read error: %s", e)

            # Cache miss — run function
            result = await func(*args, **kwargs)

            try:
                serialized = json.dumps(result, cls=_DecimalEncoder, default=str)
                await r.setex(cache_key, timedelta(seconds=ttl), serialized)
                logger.debug("Cache SET: %s (ttl=%ds)", cache_key, ttl)
            except Exception as e:
                logger.warning("Cache write error: %s", e)

            return result

        return wrapper
    return decorator


async def invalidate_cache(prefix: str):
    """
    Invalidate all cache keys matching a prefix.
    Call after data changes (e.g. import, category assignment).
    """
    r = await get_redis()
    if r is None:
        return

    try:
        pattern = f"{prefix}:*"
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await r.scan(cursor, match=pattern, count=100)
            if keys:
                await r.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        if deleted:
            logger.info("Cache invalidated: %s (%d keys)", prefix, deleted)
    except Exception as e:
        logger.warning("Cache invalidation error: %s", e)


async def close_redis():
    """Close Redis connection on shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
