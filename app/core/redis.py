"""Shared Redis client for cache, rate limiting and short-lived state (created lazily)."""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def close_redis() -> None:
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
    get_redis.cache_clear()


async def check_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
