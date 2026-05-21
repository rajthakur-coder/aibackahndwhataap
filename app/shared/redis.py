import asyncio
from weakref import WeakKeyDictionary

from redis import asyncio as aioredis

from app.config import settings


_redis_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis] = WeakKeyDictionary()


async def get_redis() -> aioredis.Redis:
    """Get or create a Redis client for the current event loop."""
    loop = asyncio.get_running_loop()
    redis = _redis_clients.get(loop)
    if redis is None:
        redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_clients[loop] = redis
    return redis


async def close_redis() -> None:
    """Close Redis clients created for active event loops."""
    clients = list(_redis_clients.values())
    _redis_clients.clear()
    for redis in clients:
        await redis.aclose()
