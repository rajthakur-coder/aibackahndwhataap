from app.db.session import Base
from app.models.automation import AutomationEvent
from app.models.automation import AutomationExecution
from app.models.automation import AutomationRule
from app.models.automation import BroadcastCampaign
from app.models.automation import MessageTemplate
from app.models.audit import AuditLog
from app.models.contact import Contact, ContactTag, Tag
from app.models.compliance import CSATResponse, CustomerConsent, DataPrincipalRequestLog, TenantCustomTool
from app.models.crm import AgentAction
from app.models.crm import Appointment
from app.models.crm import BotSettings
from app.models.crm import CustomerMemory
from app.models.crm import CustomerProfile
from app.models.crm import HandoffTicket
from app.models.crm import Lead
from app.models.crm import OrderStatus
from app.models.ecommerce import EcommerceConnection
from app.models.ecommerce import EcommerceCart
from app.models.ecommerce import EcommerceBundlePairing
from app.models.ecommerce import ContactStoreMapping
from app.models.ecommerce import EcommerceCustomer
from app.models.ecommerce import EcommerceOrder
from app.models.ecommerce import EcommerceProduct
from app.models.ecommerce import EcommerceReturnRequest
from app.models.ecommerce import ShopifyCatalogCollection
from app.models.ecommerce import ShopifyCatalogDefaultCategory
from app.models.ecommerce import ShopifyWebhookEvent
from app.models.integration import Integration
from app.models.tenants import AgencyTenantAccess, TenantConfig
from app.models.user import User
from app.models.whatsapp import Message
from app.models.whatsapp import WebhookEvent
from app.models.whatsapp import WhatsappInteractionEvent
from app.models.whatsapp import WhatsappCredential
from app.models.whatsapp import WhatsappTemplate
