from app.db.session import Base
from app.models.automation import (
    AutomationEvent,
    AutomationExecution,
    AutomationRule,
    MessageTemplate,
)
from app.models.audit import AuditLog
from app.models.contact import Contact, ContactTag, Tag
from app.models.compliance import CSATResponse, CustomerConsent, DataPrincipalRequestLog, TenantCustomTool
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
    ContactStoreMapping,
    EcommerceCart,
    EcommerceBundlePairing,
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
    EcommerceProduct,
    EcommerceReturnRequest,
    ShopifyCatalogCollection,
    ShopifyCatalogDefaultCategory,
    ShopifyWebhookEvent,
)
from app.models.knowledge import KnowledgeBase
from app.models.integration import Integration
from app.models.tenants import AgencyTenantAccess, TenantConfig
from app.models.user import User
from app.models.whatsapp import (
    Message,
    WebhookEvent,
    WhatsappCredential,
    WhatsappInteractionEvent,
    WhatsappTemplate,
)

__all__ = [
    "AgentAction",
    "Appointment",
    "AutomationEvent",
    "AutomationExecution",
    "AutomationRule",
    "AuditLog",
    "Base",
    "BotSettings",
    "Contact",
    "CSATResponse",
    "CustomerConsent",
    "DataPrincipalRequestLog",
    "TenantCustomTool",
    "ContactStoreMapping",
    "ContactTag",
    "Tag",
    "TenantConfig",
    "AgencyTenantAccess",
    "User",
    "EcommerceCart",
    "EcommerceBundlePairing",
    "CustomerMemory",
    "CustomerProfile",
    "EcommerceConnection",
    "EcommerceCustomer",
    "EcommerceOrder",
    "EcommerceProduct",
    "EcommerceReturnRequest",
    "HandoffTicket",
    "Integration",
    "KnowledgeBase",
    "Lead",
    "Message",
    "MessageTemplate",
    "OrderStatus",
    "ShopifyWebhookEvent",
    "ShopifyCatalogCollection",
    "ShopifyCatalogDefaultCategory",
    "WebhookEvent",
    "WhatsappCredential",
    "WhatsappInteractionEvent",
    "WhatsappTemplate",
]
