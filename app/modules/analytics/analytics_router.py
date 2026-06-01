from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.analytics.analytics_schema import CSATRequest
from app.modules.analytics.analytics_service import commerce_dashboard, record_csat
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


analytics_router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_user_token)])


@analytics_router.get("/dashboard")
async def dashboard(days: int = 30, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: commerce_dashboard(sync_db, tenant_id=tenant_id, days=days))


@analytics_router.post("/csat")
async def submit_csat(data: CSATRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: record_csat(sync_db, tenant_id, data.phone, data.rating, data.comment, data.conversation_id))
