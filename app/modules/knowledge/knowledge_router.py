from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security import get_current_user_token
from app.modules.audit import write_async_audit_log
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.modules.knowledge.knowledge_service import (
    get_or_create_knowledge_base,
    save_knowledge_base,
    serialize_knowledge_base,
)
from app.shared.tenant import strict_tenant_id


knowledge_router = APIRouter(
    prefix="/knowledge-base",
    tags=["knowledge-base"],
    dependencies=[Depends(get_current_user_token)],
)


@knowledge_router.get("")
async def get_knowledge_base(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: serialize_knowledge_base(get_or_create_knowledge_base(sync_db, tenant_id)))


@knowledge_router.put("")
async def update_knowledge_base(
    data: KnowledgeBaseRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    saved = await db.run_sync(lambda sync_db: save_knowledge_base(sync_db, data, tenant_id))
    await write_async_audit_log(
        db,
        action="knowledge_base.updated",
        tenant_id=tenant_id,
        entity_type="knowledge_base",
        entity_id=tenant_id,
        metadata={
            "company_name": saved.get("company_name"),
            "industry": saved.get("industry"),
            "has_website": bool(saved.get("website_link")),
        },
        commit=True,
    )
    return {
        "status": "success",
        "data": saved,
    }
