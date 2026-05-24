from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.modules.knowledge.knowledge_service import (
    get_or_create_knowledge_base,
    save_knowledge_base,
    serialize_knowledge_base,
)


knowledge_router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


@knowledge_router.get("")
async def get_knowledge_base(db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: serialize_knowledge_base(get_or_create_knowledge_base(sync_db)))


@knowledge_router.put("")
async def update_knowledge_base(data: KnowledgeBaseRequest, db: AsyncSession = Depends(get_db)):
    return {
        "status": "success",
        "data": await db.run_sync(lambda sync_db: save_knowledge_base(sync_db, data)),
    }
