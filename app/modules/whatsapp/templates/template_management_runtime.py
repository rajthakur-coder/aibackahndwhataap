import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import Message, WhatsappTemplate
from app.modules.whatsapp.live_chat.socket import live_chat_manager
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential


REQUEST_TIMEOUT = 30
TEMPLATE_STATUSES = {"PENDING", "APPROVED", "REJECTED", "IN_REVIEW"}
logger = logging.getLogger(__name__)


from app.modules.whatsapp.templates.graph_service import *
from app.modules.whatsapp.templates.repository_service import *
from app.modules.whatsapp.templates.sender_service import *











































