import inspect

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.modules.automation.automation_router import automation_router
from app.modules.analytics import analytics_router
from app.modules.compliance import compliance_router
from app.modules.headless import headless_router
from app.modules.auth.auth_router import auth_router
from app.modules.crm.crm_router import crm_router
from app.modules.ecommerce.ecommerce_router import ecommerce_router, shopify_webhooks_router
from app.modules.integrations.providers.shopify.shopify_router import shopify_integration_router
from app.modules.integrations.providers.whatsapp_business.whatsapp_business_router import whatsapp_business_router
from app.modules.integrations.providers.woocommerce.woocommerce_router import woocommerce_integration_router
from app.modules.knowledge.knowledge_router import knowledge_router
from app.modules.onboarding import onboarding_router
from app.modules.scraper.scraper_router import scraper_router
from app.modules.system.system_router import system_router
from app.modules.tenants import tenants_router
from app.modules.tenants.agency_router import agency_router
from app.modules.v1.v1_router import internal_router, v1_router
from app.modules.whatsapp.analytics.analytics_router import whatsapp_analytics_router
from app.modules.whatsapp.live_chat.live_chat_router import websocket_router as whatsapp_live_chat_websocket_router
from app.modules.whatsapp.whatsapp_router import whatsapp_router
from app.modules.whatsapp.webhooks.routing.webhook_router import whatsapp_webhook_router
from app.shared.arq_queue import close_arq_pools
from app.shared.logging import setup_logging
from app.shared.redis import close_redis


setup_logging()

app = FastAPI(
    title="AI WhatsApp Automation API",
    version="1.0.0",
    debug=settings.DEBUG,
)

origins = settings.CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    app.state.arq_pool = await create_pool(
        RedisSettings.from_dsn(settings.REDIS_URL),
        default_queue_name=settings.ARQ_QUEUE_NAME,
    )


@app.on_event("shutdown")
async def shutdown():
    arq_pool = getattr(app.state, "arq_pool", None)
    if arq_pool is not None:
        close = getattr(arq_pool, "aclose", None) or getattr(arq_pool, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
    await close_arq_pools()
    await close_redis()

app.include_router(system_router)
app.include_router(auth_router)
app.include_router(whatsapp_router)
app.include_router(whatsapp_live_chat_websocket_router)
app.include_router(whatsapp_analytics_router)
app.include_router(whatsapp_webhook_router)
app.include_router(ecommerce_router)
app.include_router(shopify_webhooks_router)
app.include_router(shopify_integration_router)
app.include_router(whatsapp_business_router)
app.include_router(woocommerce_integration_router)
app.include_router(scraper_router)
app.include_router(knowledge_router)
app.include_router(onboarding_router)
app.include_router(automation_router)
app.include_router(crm_router)
app.include_router(tenants_router)
app.include_router(agency_router)
app.include_router(analytics_router)
app.include_router(compliance_router)
app.include_router(headless_router)
app.include_router(v1_router)
app.include_router(internal_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
    )
