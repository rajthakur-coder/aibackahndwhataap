from fastapi import APIRouter, Depends

from app.security import get_current_user_token
from app.modules.whatsapp.setup.credentials_router import router as whatsapp_credentials_router
from app.modules.whatsapp.live_chat.live_chat_router import router as whatsapp_live_chat_router
from app.modules.whatsapp.messages.messages_router import router as whatsapp_messages_router
from app.modules.whatsapp.templates.templates_router import router as whatsapp_templates_router


whatsapp_router = APIRouter(tags=["whatsapp"], dependencies=[Depends(get_current_user_token)])
whatsapp_router.include_router(whatsapp_live_chat_router)
whatsapp_router.include_router(whatsapp_credentials_router)
whatsapp_router.include_router(whatsapp_templates_router)
whatsapp_router.include_router(whatsapp_messages_router)
