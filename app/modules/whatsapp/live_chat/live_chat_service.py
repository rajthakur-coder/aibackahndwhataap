from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contact import Contact, ContactTag, Tag
from app.models.whatsapp import Message
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential


from app.modules.whatsapp.live_chat.contact_service import *
from app.modules.whatsapp.live_chat.message_service import *
from app.modules.whatsapp.live_chat.tag_service import *
