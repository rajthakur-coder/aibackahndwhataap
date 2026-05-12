from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    direction = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, default="whatsapp", index=True)
    external_id = Column(String, unique=True, index=True, nullable=True)
    phone = Column(String, index=True, nullable=True)
    message_text = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True)
    name = Column(String, nullable=True)


class ScrapedData(Base):
    __tablename__ = "scraped_data"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False, index=True)
    max_pages = Column(Integer, nullable=False)
    status = Column(String, default="queued", index=True)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScrapedChunk(Base):
    __tablename__ = "scraped_chunks"

    id = Column(Integer, primary_key=True, index=True)
    scraped_data_id = Column(Integer, nullable=False, index=True)
    url = Column(String, nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CustomerMemory(Base):
    __tablename__ = "customer_memories"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    memory_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="new")
    source = Column(String, default="whatsapp")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    customer_name = Column(String, nullable=True)
    requested_time = Column(String, nullable=True)
    status = Column(String, default="requested")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderStatus(Base):
    __tablename__ = "order_statuses"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=True)
    order_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="received")
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EcommerceConnection(TimestampMixin, Base):
    __tablename__ = "ecommerce_connections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    platform = Column(String, index=True, nullable=False)
    store_url = Column(String, nullable=False)
    access_token = Column(Text, nullable=True)
    consumer_key = Column(Text, nullable=True)
    consumer_secret = Column(Text, nullable=True)
    status = Column(String, default="active")
    last_sync_at = Column(DateTime, nullable=True)


class EcommerceOrder(TimestampMixin, Base):
    __tablename__ = "ecommerce_orders"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True, nullable=False)
    order_number = Column(String, index=True, nullable=False)
    phone = Column(String, index=True, nullable=True)
    email = Column(String, index=True, nullable=True)
    customer_name = Column(String, nullable=True)
    status = Column(String, index=True, nullable=True)
    fulfillment_status = Column(String, index=True, nullable=True)
    financial_status = Column(String, nullable=True)
    total = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    tracking_url = Column(Text, nullable=True)
    items = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    delivered_message_sent_at = Column(DateTime, nullable=True)


class EcommerceProduct(TimestampMixin, Base):
    __tablename__ = "ecommerce_products"

    id = Column(Integer, primary_key=True, index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True, nullable=False)
    title = Column(String, index=True, nullable=False)
    handle = Column(String, nullable=True, index=True)
    product_url = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    vendor = Column(String, nullable=True)
    product_type = Column(String, nullable=True, index=True)
    tags = Column(Text, nullable=True)
    status = Column(String, nullable=True, index=True)
    price_min = Column(String, nullable=True)
    price_max = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    sku = Column(String, nullable=True, index=True)
    inventory = Column(String, nullable=True)
    image_urls = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)


class HandoffTicket(Base):
    __tablename__ = "handoff_tickets"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, default="open")
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=True)
    action_type = Column(String, index=True, nullable=False)
    status = Column(String, default="logged")
    payload = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class MessageTemplate(TimestampMixin, Base):
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    body = Column(Text, nullable=False)
    channel = Column(String, default="whatsapp", index=True)
    template_type = Column(String, default="text", index=True)
    provider_template_name = Column(String, nullable=True, index=True)
    language = Column(String, default="en")
    body_variable_order = Column(Text, nullable=True)
    status = Column(String, default="active", index=True)


class AutomationRule(TimestampMixin, Base):
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    trigger = Column(String, index=True, nullable=False)
    message_template_id = Column(Integer, nullable=True, index=True)
    message_body = Column(Text, nullable=True)
    delay_seconds = Column(Integer, default=0)
    conditions = Column(Text, nullable=True)
    enabled = Column(String, default="true", index=True)


class AutomationEvent(TimestampMixin, Base):
    __tablename__ = "automation_events"

    id = Column(Integer, primary_key=True, index=True)
    trigger = Column(String, index=True, nullable=False)
    source = Column(String, default="system", index=True)
    external_id = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True, index=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    scheduled_for = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class AutomationExecution(TimestampMixin, Base):
    __tablename__ = "automation_executions"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, index=True, nullable=False)
    rule_id = Column(Integer, index=True, nullable=False)
    phone = Column(String, nullable=True, index=True)
    status = Column(String, default="pending", index=True)
    rendered_message = Column(Text, nullable=True)
    provider_response = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    source = Column(String, nullable=True, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    title = Column(String, nullable=False)
    source = Column(String, nullable=True, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class StructuredProduct(Base):
    __tablename__ = "structured_products"

    id = Column(Integer, primary_key=True, index=True)
    source_url = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=True, index=True)
    brand = Column(String, nullable=True, index=True)
    price = Column(String, nullable=True)
    image_urls = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FAQ(Base):
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, index=True)
    source_url = Column(String, nullable=False, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    category = Column(String, nullable=True, index=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Policy(Base):
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, index=True)
    source_url = Column(String, nullable=False, index=True)
    policy_type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    source_url = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=True, index=True)
    price = Column(String, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

