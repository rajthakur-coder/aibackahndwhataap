from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.onboarding.onboarding_schema import (
    ApplyBundleSuggestionsRequest,
    BundleSuggestionRequest,
    OnboardingPreviewRequest,
    OnboardingStepUpdateRequest,
    WebsiteAssistRequest,
)
from app.modules.onboarding.onboarding_service import (
    ai_assist_from_website,
    apply_bundle_suggestions,
    go_live_readiness,
    mark_go_live,
    onboarding_wizard,
    onboarding_status,
    preview_onboarding_bot,
    suggest_bundle_pairings,
    update_onboarding_step,
)
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


onboarding_router = APIRouter(prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(get_current_user_token)])


@onboarding_router.get("/status")
async def status(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: onboarding_status(sync_db, tenant_id))


@onboarding_router.get("/wizard")
async def wizard(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: onboarding_wizard(sync_db, tenant_id))


@onboarding_router.post("/steps/{step_key}")
async def update_step(step_key: str, data: OnboardingStepUpdateRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await db.run_sync(lambda sync_db: update_onboarding_step(sync_db, tenant_id, step_key, data.status, data.data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@onboarding_router.post("/ai-assist/from-website")
async def website_assist(data: WebsiteAssistRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ai_assist_from_website(db, tenant_id, data.website_url, apply=data.apply)


@onboarding_router.post("/suggest-bundles")
async def suggest_bundles(data: BundleSuggestionRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    suggestions = await db.run_sync(lambda sync_db: suggest_bundle_pairings(sync_db, tenant_id, data.limit))
    if data.apply:
        applied = await db.run_sync(lambda sync_db: apply_bundle_suggestions(sync_db, tenant_id, suggestions, data.discount_type, data.discount_value))
        return {"status": "success", "tenant_id": tenant_id, "suggestions": suggestions, "applied": applied}
    return {"status": "success", "tenant_id": tenant_id, "suggestions": suggestions}


@onboarding_router.post("/apply-bundles")
async def apply_bundles(data: ApplyBundleSuggestionsRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: apply_bundle_suggestions(sync_db, tenant_id, data.suggestions, data.discount_type, data.discount_value))


@onboarding_router.post("/preview-test")
async def preview_test(data: OnboardingPreviewRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: preview_onboarding_bot(sync_db, tenant_id, data.message, data.phone, data.channel))


@onboarding_router.get("/go-live/readiness")
async def go_live_status(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: go_live_readiness(sync_db, tenant_id))


@onboarding_router.post("/go-live")
async def go_live(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await db.run_sync(lambda sync_db: mark_go_live(sync_db, tenant_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
