"""Backward-compatible schema exports.

New code should import request/response schemas from the owning module, such as
``app.modules.whatsapp.whatsapp_schema``.
"""

from app.modules.automation.automation_schema import (
    AbandonedCartRequest,
    AutomationEventRequest,
    AutomationRuleRequest,
    AutomationRuleUpdateRequest,
    MessageTemplateRequest,
    SendTemplateRequest,
)
from app.modules.crm.crm_schema import ActionRequest, OrderRequest
from app.modules.ecommerce.ecommerce_schema import (
    DeliveredFollowupRequest,
    EcommerceConnectionRequest,
    EcommerceConnectionUpdateRequest,
    EcommerceProductSyncRequest,
    EcommerceSyncRequest,
)
from app.modules.rag.rag_schema import DocumentRequest, ScrapeRequest
from app.modules.whatsapp.whatsapp_schema import (
    RetryWebhookEventsRequest,
    SendMessageRequest,
)

__all__ = [
    "AbandonedCartRequest",
    "ActionRequest",
    "AutomationEventRequest",
    "AutomationRuleRequest",
    "AutomationRuleUpdateRequest",
    "DeliveredFollowupRequest",
    "DocumentRequest",
    "EcommerceConnectionRequest",
    "EcommerceConnectionUpdateRequest",
    "EcommerceProductSyncRequest",
    "EcommerceSyncRequest",
    "MessageTemplateRequest",
    "OrderRequest",
    "RetryWebhookEventsRequest",
    "ScrapeRequest",
    "SendMessageRequest",
    "SendTemplateRequest",
]
