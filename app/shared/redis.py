from redis import asyncio as aioredis

from app.config import settings


_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create a shared Redis client connection."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client connection."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
