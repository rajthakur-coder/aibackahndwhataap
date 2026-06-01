from fastapi import APIRouter, Depends

from app.security import get_current_user_token
from app.modules.ecommerce.catalog.catalog_router import router as ecommerce_catalog_router
from app.modules.ecommerce.bundles.bundle_router import router as ecommerce_bundle_router
from app.modules.ecommerce.connections.connection_router import router as ecommerce_connection_router
from app.modules.ecommerce.orders.order_router import router as ecommerce_order_router
from app.modules.ecommerce.webhooks.ecommerce_webhook_router import router as ecommerce_webhook_router
from app.modules.ecommerce.webhooks.shopify_webhook_router import router as shopify_webhook_events_router


ecommerce_router = APIRouter(prefix="/ecommerce", tags=["ecommerce"], dependencies=[Depends(get_current_user_token)])
ecommerce_router.include_router(ecommerce_connection_router)
ecommerce_router.include_router(ecommerce_catalog_router)
ecommerce_router.include_router(ecommerce_bundle_router)
ecommerce_router.include_router(ecommerce_webhook_router)
ecommerce_router.include_router(ecommerce_order_router)

shopify_webhooks_router = APIRouter(prefix="/webhooks/shopify", tags=["shopify-webhooks"])
shopify_webhooks_router.include_router(shopify_webhook_events_router)
