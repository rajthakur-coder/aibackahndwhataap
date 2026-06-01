from fastapi import APIRouter, Depends

from app.security import get_current_user_token
from app.modules.crm.agent_actions.agent_actions_router import router as crm_agent_actions_router
from app.modules.crm.handoffs.handoff_router import router as crm_handoff_router
from app.modules.crm.records.records_router import router as crm_records_router
from app.modules.crm.settings.bot_settings_router import router as crm_bot_settings_router


crm_router = APIRouter(tags=["crm"], dependencies=[Depends(get_current_user_token)])
crm_router.include_router(crm_bot_settings_router)
crm_router.include_router(crm_records_router)
crm_router.include_router(crm_handoff_router)
crm_router.include_router(crm_agent_actions_router)
