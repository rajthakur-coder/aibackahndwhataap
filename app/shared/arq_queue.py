import asyncio
from weakref import WeakKeyDictionary

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


_arq_pools: WeakKeyDictionary[asyncio.AbstractEventLoop, ArqRedis] = WeakKeyDictionary()


def arq_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.REDIS_URL)


async def get_arq_pool() -> ArqRedis:
    loop = asyncio.get_running_loop()
    pool = _arq_pools.get(loop)
    if pool is None:
        pool = await create_pool(
            arq_redis_settings(),
            default_queue_name=settings.arq_queue_name,
        )
        _arq_pools[loop] = pool
    return pool


async def close_arq_pools() -> None:
    pools = list(_arq_pools.values())
    _arq_pools.clear()
    for pool in pools:
        await pool.aclose()


async def enqueue_whatsapp_webhook_event(event_id: int, *, unique: bool = True) -> str | None:
    redis = await get_arq_pool()
    job = await redis.enqueue_job(
        "process_whatsapp_webhook_event",
        event_id,
        _job_id=f"whatsapp-webhook:{event_id}" if unique else None,
        _queue_name=settings.arq_queue_name,
        _expires=settings.arq_job_timeout_seconds * 3,
    )
    return job.job_id if job else None


async def enqueue_whatsapp_cross_sell(
    phone: str,
    text: str,
    base_products: list[dict],
) -> str | None:
    redis = await get_arq_pool()
    job = await redis.enqueue_job(
        "process_whatsapp_cross_sell",
        phone,
        text,
        base_products[:5],
        _queue_name=settings.arq_queue_name,
        _expires=settings.arq_job_timeout_seconds * 3,
    )
    return job.job_id if job else None


async def enqueue_whatsapp_product_images(
    phone: str,
    products: list[dict],
    caption_mode: str,
    failure_action: str,
) -> str | None:
    redis = await get_arq_pool()
    job = await redis.enqueue_job(
        "process_whatsapp_product_images",
        phone,
        products[:2],
        caption_mode,
        failure_action,
        _queue_name=settings.arq_queue_name,
        _expires=settings.arq_job_timeout_seconds * 3,
    )
    return job.job_id if job else None
