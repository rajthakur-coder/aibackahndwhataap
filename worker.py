from app.config import settings
from app.modules.whatsapp.core.webhook_job_service import (
    process_whatsapp_cross_sell,
    process_whatsapp_product_images,
    process_whatsapp_webhook_event,
)
from app.shared.arq_queue import arq_redis_settings


class WorkerSettings:
    functions = [
        process_whatsapp_webhook_event,
        process_whatsapp_cross_sell,
        process_whatsapp_product_images,
    ]
    redis_settings = arq_redis_settings()
    queue_name = settings.arq_queue_name
    job_timeout = settings.arq_job_timeout_seconds
    max_jobs = settings.arq_max_jobs
    max_tries = 3
    keep_result = 3600
