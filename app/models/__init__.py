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
    ShopifyWebhookEvent,
)
from app.models.rag import (
    FAQ,
    KnowledgeChunk,
    KnowledgeDocument,
    Policy,
    ScrapedChunk,
    ScrapedData,
    ScrapeJob,
    Service,
    StructuredProduct,
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
    "Contact",
    "ContactTag",
    "Tag",
    "CustomerMemory",
    "CustomerProfile",
    "EcommerceConnection",
    "EcommerceCustomer",
    "EcommerceOrder",
    "EcommerceProduct",
    "FAQ",
    "HandoffTicket",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Lead",
    "Message",
    "MessageTemplate",
    "OrderStatus",
    "Policy",
    "ScrapedChunk",
    "ScrapedData",
    "Service",
    "StructuredProduct",
    "ShopifyWebhookEvent",
    "WebhookEvent",
    "WhatsappCredential",
    "WhatsappTemplate",
]
