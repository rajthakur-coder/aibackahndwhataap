from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.headless.custom_tool_service import list_custom_tools, upsert_custom_tool
from app.modules.headless.llm_provider import llm_provider_registry
from app.modules.headless.oms_adapter import oms_adapter_registry
from app.modules.headless.onboarding_assist_service import (
    BundleApplyRequest,
    BundleSuggestRequest,
    FAQAssistRequest,
    WebsiteAssistRequest,
    apply_bundle_suggestions,
    build_website_onboarding_assist,
    faq_onboarding_assist,
    save_website_onboarding_assist,
    suggest_bundle_pairings,
)
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config, upsert_tenant_config
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


headless_router = APIRouter(prefix="/headless", tags=["headless"], dependencies=[Depends(get_current_user_token)])


@headless_router.get("/oms/adapters")
async def oms_adapters():
    return {"platforms": oms_adapter_registry.list_platforms() or ["shopify", "woocommerce", "custom_rest"]}


@headless_router.get("/llm/providers")
async def llm_providers():
    return llm_provider_registry.list_providers()


@headless_router.get("/tools")
async def tools(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: list_custom_tools(sync_db, tenant_id))


@headless_router.post("/tools")
async def save_tool(request: Request, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    return await db.run_sync(lambda sync_db: upsert_custom_tool(sync_db, tenant_id, payload))


@headless_router.get("/onboarding")
async def onboarding(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    config = await db.run_sync(lambda sync_db: get_tenant_config(sync_db, tenant_id))
    data = serialize_tenant_config(config) if config else {"tenant_id": tenant_id, "metadata": {}}
    metadata = data.get("metadata") or {}
    return metadata.get("onboarding") or {
        "connect_whatsapp": False,
        "connect_oms": False,
        "import_catalog": False,
        "brand_voice": bool(data.get("brand_voice_prompt")),
        "policies": bool(data.get("return_policy") or data.get("shipping_policy")),
        "discounts": bool(data.get("discount_rules")),
        "bundle_pairs": False,
        "templates": False,
        "go_live": False,
    }


@headless_router.post("/onboarding/ai-assist/website")
async def website_assist(
    data: WebsiteAssistRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    scraped, scrape_status = await build_website_onboarding_assist(data)
    return await db.run_sync(
        lambda sync_db: save_website_onboarding_assist(sync_db, tenant_id, data, scraped, scrape_status)
    )


@headless_router.post("/onboarding/ai-assist/faqs")
async def faq_assist(
    data: FAQAssistRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: faq_onboarding_assist(sync_db, tenant_id, data))


@headless_router.post("/onboarding/ai-assist/bundles/suggest")
async def bundle_assist_suggest(
    data: BundleSuggestRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: suggest_bundle_pairings(sync_db, tenant_id, data))


@headless_router.post("/onboarding/ai-assist/bundles/apply")
async def bundle_assist_apply(
    data: BundleApplyRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: apply_bundle_suggestions(sync_db, tenant_id, data))


@headless_router.put("/settings")
async def update_headless_settings(request: Request, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    payload = await request.json()

    def sync_op(sync_db):
        config = get_tenant_config(sync_db, tenant_id)
        existing = serialize_tenant_config(config) if config else {"metadata": {}, "brand_name": tenant_id}
        metadata = existing.get("metadata") or {}
        metadata.update(
            {
                "onboarding": payload.get("onboarding", metadata.get("onboarding", {})),
                "agency": payload.get("agency", metadata.get("agency", {})),
                "white_label": payload.get("white_label", metadata.get("white_label", {})),
                "flow_settings": payload.get("flow_settings", metadata.get("flow_settings", {})),
                "llm": payload.get("llm", metadata.get("llm", {})),
            }
        )
        return serialize_tenant_config(upsert_tenant_config(sync_db, {"brand_name": existing.get("brand_name") or tenant_id, "metadata": metadata}, tenant_id))

    return await db.run_sync(sync_op)
