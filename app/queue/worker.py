import logging

from arq.connections import RedisSettings

from app.config import settings
from app.modules.whatsapp.webhooks.events.job_service import (
    process_whatsapp_cross_sell,
    process_whatsapp_product_images,
    process_whatsapp_webhook_event,
)
from app.shared.logging import setup_logging


setup_logging()
log = logging.getLogger("arq.worker")


async def startup(ctx):
    log.info("ARQ worker starting up")


async def shutdown(ctx):
    log.info("ARQ worker shutting down")


class WorkerSettings:
    functions = [
        process_whatsapp_webhook_event,
        process_whatsapp_cross_sell,
        process_whatsapp_product_images,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = settings.ARQ_QUEUE_NAME
    job_timeout = settings.ARQ_DEFAULT_TIMEOUT
    max_jobs = settings.MAX_CONCURRENT_JOBS
    max_tries = settings.JOB_MAX_ATTEMPTS
    keep_result = 3600
