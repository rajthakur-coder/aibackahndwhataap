import asyncio
from datetime import datetime, timezone
from weakref import WeakKeyDictionary

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


_arq_pools: WeakKeyDictionary[asyncio.AbstractEventLoop, ArqRedis] = WeakKeyDictionary()
_redis_settings: RedisSettings | None = None


def arq_redis_settings() -> RedisSettings:
    global _redis_settings
    if _redis_settings is None:
        _redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    return _redis_settings


async def get_arq_pool() -> ArqRedis:
    loop = asyncio.get_running_loop()
    pool = _arq_pools.get(loop)
    if pool is not None:
        return pool

    pool = await create_pool(
        arq_redis_settings(),
        default_queue_name=settings.ARQ_QUEUE_NAME,
    )
    _arq_pools[loop] = pool
    return pool


async def close_arq_pools() -> None:
    pools = list(_arq_pools.values())
    _arq_pools.clear()

    for pool in pools:
        try:
            await pool.aclose()
        except Exception:
            pass


async def enqueue_whatsapp_webhook_event(event_id: int, *, unique: bool = True) -> str | None:
    redis = await get_arq_pool()
    job = await redis.enqueue_job(
        "process_whatsapp_webhook_event",
        event_id,
        _job_id=f"whatsapp-webhook:{event_id}" if unique else None,
        _queue_name=settings.ARQ_QUEUE_NAME,
        _expires=settings.ARQ_DEFAULT_TIMEOUT * 3,
    )
    return job.job_id if job else None


async def enqueue_whatsapp_cross_sell(
    phone: str,
    text: str,
    base_products: list[dict],
    delay_seconds: int = 0,
) -> str | None:
    redis = await get_arq_pool()
    queued_at = datetime.now(timezone.utc).isoformat()
    job = await redis.enqueue_job(
        "process_whatsapp_cross_sell",
        phone,
        text[:1000],
        base_products[:5],
        queued_at,
        _job_id=f"cross-sell:{phone}:{hash(text)}:{int(delay_seconds)}",
        _queue_name=settings.ARQ_QUEUE_NAME,
        _expires=settings.ARQ_DEFAULT_TIMEOUT * 3,
        _defer_by=max(0, delay_seconds),
    )
    return job.job_id if job else None


async def enqueue_whatsapp_product_images(
    phone: str,
    products: list[dict],
    caption_mode: str,
    failure_action: str,
) -> str | None:
    redis = await get_arq_pool()
    safe_products = products[:2]
    product_ids = "-".join(str(p.get("id") or p.get("sku") or "") for p in safe_products)
    job = await redis.enqueue_job(
        "process_whatsapp_product_images",
        phone,
        safe_products,
        caption_mode,
        failure_action,
        _job_id=f"product-images:{phone}:{product_ids}:{caption_mode}",
        _queue_name=settings.ARQ_QUEUE_NAME,
        _expires=settings.ARQ_DEFAULT_TIMEOUT * 3,
    )
    return job.job_id if job else None
