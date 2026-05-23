from app.db.session import Base
from app.models.automation import (
    AutomationEvent,
    AutomationExecution,
    AutomationRule,
    MessageTemplate,
)
from app.models.contact import Contact, ContactTag, Tag
from app.models.crm import (
    AgentAction,
    Appointment,
    BotSettings,
    CustomerMemory,
    CustomerProfile,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.models.ecommerce import (
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
    EcommerceProduct,
    ShopifyCatalogCollection,
    ShopifyWebhookEvent,
)
from app.models.whatsapp import (
    Message,
    WebhookEvent,
    WhatsappCredential,
    WhatsappTemplate,
)

__all__ = [
    "AgentAction",
    "Appointment",
    "AutomationEvent",
    "AutomationExecution",
    "AutomationRule",
    "Base",
    "BotSettings",
    "Contact",
    "ContactTag",
    "Tag",
    "CustomerMemory",
    "CustomerProfile",
    "EcommerceConnection",
    "EcommerceCustomer",
    "EcommerceOrder",
    "EcommerceProduct",
    "HandoffTicket",
    "Lead",
    "Message",
    "MessageTemplate",
    "OrderStatus",
    "ShopifyWebhookEvent",
    "ShopifyCatalogCollection",
    "WebhookEvent",
    "WhatsappCredential",
    "WhatsappTemplate",
]
