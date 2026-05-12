import os
import json
import asyncio
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel as PydanticBaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from agent_service import (
    log_crm_update,
    log_email_request,
    log_payment_link_request,
    process_agent_message,
)
from database import SessionLocal, engine, get_db
from ecommerce_service import (
    create_connection,
    product_knowledge_text,
    send_delivered_followups,
    sync_orders,
    sync_products,
    test_connection,
    update_connection,
    upsert_order as upsert_ecommerce_order,
)
from models import (
    AgentAction,
    Appointment,
    Base,
    CustomerMemory,
    EcommerceConnection,
    EcommerceOrder,
    EcommerceProduct,
    HandoffTicket,
    KnowledgeDocument,
    Lead,
    Message,
    OrderStatus,
    ScrapedData,
    WebhookEvent,
)
from openai_service import generate_ai_reply
from pinecone_service import status as pinecone_status
from rag_service import (
    find_relevant_catalog_products,
    find_relevant_product_image,
    save_knowledge_document,
    save_knowledge_chunks,
    save_scraped_chunks,
)
from scraper import crawl_website
from whatsapp_service import send_whatsapp_image, send_whatsapp_message

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI WhatsApp Automation API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ecommerce_auto_sync_enabled() -> bool:
    return os.getenv("ECOMMERCE_AUTO_SYNC_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ecommerce_auto_sync_interval() -> int:
    return max(60, int(os.getenv("ECOMMERCE_AUTO_SYNC_INTERVAL_SECONDS", "300")))


def ecommerce_auto_sync_limit() -> int:
    return max(1, min(int(os.getenv("ECOMMERCE_AUTO_SYNC_LIMIT", "50")), 100))


def ecommerce_auto_sync_products_enabled() -> bool:
    return os.getenv("ECOMMERCE_AUTO_SYNC_PRODUCTS_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ecommerce_auto_sync_product_limit() -> int:
    return max(1, min(int(os.getenv("ECOMMERCE_AUTO_SYNC_PRODUCT_LIMIT", "100")), 250))


def sync_product_catalog_knowledge(
    db: Session,
    connection: EcommerceConnection,
    limit: int,
) -> dict:
    products = (
        db.query(EcommerceProduct)
        .filter(EcommerceProduct.connection_id == connection.id)
        .order_by(EcommerceProduct.updated_at.desc())
        .limit(max(1, min(limit, 250)))
        .all()
    )
    source = f"ecommerce://{connection.platform}/{connection.id}/products"
    content = "\n\n---\n\n".join(product_knowledge_text(product) for product in products)
    if not content:
        return {"knowledge_source": source, "knowledge_products": 0}

    document = (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.source == source)
        .first()
    )
    if document:
        document.title = f"{connection.name} product catalog"
        document.content = content
        db.commit()
        db.refresh(document)
        save_knowledge_chunks(db, document)
    else:
        save_knowledge_document(
            db,
            title=f"{connection.name} product catalog",
            source=source,
            content=content,
        )

    return {"knowledge_source": source, "knowledge_products": len(products)}


def sync_active_ecommerce_connections() -> dict:
    db = SessionLocal()
    try:
        connections = (
            db.query(EcommerceConnection)
            .filter(EcommerceConnection.status == "active")
            .order_by(EcommerceConnection.updated_at.asc())
            .all()
        )
        results = []
        synced = 0
        failed = 0
        for connection in connections:
            try:
                result = sync_orders(db, connection, ecommerce_auto_sync_limit())
                if ecommerce_auto_sync_products_enabled():
                    product_result = sync_products(db, connection, ecommerce_auto_sync_product_limit())
                    product_result.update(
                        sync_product_catalog_knowledge(
                            db,
                            connection,
                            ecommerce_auto_sync_product_limit(),
                        )
                    )
                    result["products"] = product_result
                synced += result.get("synced", 0)
                results.append({"connection_id": connection.id, **result})
            except Exception as exc:
                failed += 1
                db.add(
                    AgentAction(
                        action_type="ecommerce_auto_sync_failed",
                        status="failed",
                        payload=json.dumps({"connection_id": connection.id}),
                        result=json.dumps({"error": str(exc)}),
                    )
                )
                db.commit()
                results.append(
                    {
                        "connection_id": connection.id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        return {
            "status": "completed",
            "connections": len(connections),
            "synced": synced,
            "failed": failed,
            "results": results,
        }
    finally:
        db.close()


async def ecommerce_auto_sync_loop() -> None:
    await asyncio.sleep(5)
    while ecommerce_auto_sync_enabled():
        await run_in_threadpool(sync_active_ecommerce_connections)
        await asyncio.sleep(ecommerce_auto_sync_interval())


@app.on_event("startup")
async def start_ecommerce_auto_sync() -> None:
    if ecommerce_auto_sync_enabled():
        asyncio.create_task(ecommerce_auto_sync_loop())


class SendMessageRequest(PydanticBaseModel):
    phone: str
    message: str


class ScrapeRequest(PydanticBaseModel):
    url: str
    max_pages: int = 80


class DocumentRequest(PydanticBaseModel):
    title: str
    content: str
    source: str | None = None


class OrderRequest(PydanticBaseModel):
    order_id: str
    status: str
    phone: str | None = None
    details: str | None = None


class ActionRequest(PydanticBaseModel):
    phone: str
    payload: dict


class EcommerceConnectionRequest(PydanticBaseModel):
    name: str
    platform: str
    store_url: str
    access_token: str | None = None
    consumer_key: str | None = None
    consumer_secret: str | None = None


class EcommerceConnectionUpdateRequest(PydanticBaseModel):
    name: str | None = None
    store_url: str | None = None
    access_token: str | None = None
    consumer_key: str | None = None
    consumer_secret: str | None = None
    status: str | None = None


class EcommerceSyncRequest(PydanticBaseModel):
    limit: int = 50


class EcommerceProductSyncRequest(PydanticBaseModel):
    limit: int = 100


class DeliveredFollowupRequest(PydanticBaseModel):
    limit: int = 25


class RetryWebhookEventsRequest(PydanticBaseModel):
    limit: int = 25


def save_message(db: Session, phone: str, message: str, direction: str) -> Message:
    row = Message(phone=phone, message=message, direction=direction)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def parse_whatsapp_messages(payload: dict) -> list[dict]:
    parsed_messages = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_id = message.get("id")
                text = message.get("text", {}).get("body")
                phone = message.get("from")

                if phone and text:
                    parsed_messages.append(
                        {
                            "id": message_id,
                            "phone": phone,
                            "text": text,
                            "payload": message,
                        }
                    )

    return parsed_messages


def get_or_create_webhook_event(db: Session, incoming: dict) -> tuple[WebhookEvent, bool]:
    external_id = incoming.get("id")
    event = None
    if external_id:
        event = db.query(WebhookEvent).filter(WebhookEvent.external_id == external_id).first()
    if event:
        return event, False

    event = WebhookEvent(
        external_id=external_id,
        phone=incoming["phone"],
        message_text=incoming["text"],
        payload=json.dumps(incoming.get("payload") or {}),
        status="pending",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True


def should_process_webhook_event(event: WebhookEvent, created: bool) -> bool:
    if created:
        return True
    return event.status == "failed"


async def process_webhook_event(event: WebhookEvent, db: Session) -> None:
    phone = event.phone or ""
    text = event.message_text or ""
    attempt_number = (event.attempts or 0) + 1
    event.attempts = attempt_number
    event.status = "processing"
    event.error = None
    db.commit()

    if attempt_number == 1:
        save_message(db, phone, text, "incoming")

    agent_state = process_agent_message(db, phone, text)
    catalog_products = find_relevant_catalog_products(db, text)
    if catalog_products:
        lines = ["Catalog:"]
        for index, product in enumerate(catalog_products, start=1):
            price = product.get("price_min") or ""
            if product.get("price_max") and product["price_max"] != product.get("price_min"):
                price = f"{product.get('price_min') or ''} - {product['price_max']}"
            product_line = f"{index}. {product['title']}"
            if price:
                product_line += f" - {price}"
            if product.get("product_url"):
                product_line += f"\n{product['product_url']}"
            lines.append(product_line)

        catalog_text = "\n\n".join(lines)
        await run_in_threadpool(send_whatsapp_message, phone, catalog_text)
        save_message(db, phone, catalog_text, "outgoing")

        for product in catalog_products:
            if not product.get("image_url"):
                continue
            try:
                await run_in_threadpool(
                    send_whatsapp_image,
                    phone,
                    product["image_url"],
                    product["caption"],
                )
                save_message(db, phone, f"[image] {product['caption']}", "outgoing")
            except Exception as exc:
                db.add(
                    AgentAction(
                        phone=phone,
                        action_type="catalog_image_send_failed",
                        status="failed",
                        payload=json.dumps(
                            {
                                "title": product["title"],
                                "image_url": product["image_url"],
                            }
                        ),
                        result=json.dumps({"error": str(exc)}),
                    )
                )
                db.commit()

        event.status = "processed"
        event.processed_at = datetime.utcnow()
        db.commit()
        return

    product_image = find_relevant_product_image(db, text)
    if product_image:
        try:
            await run_in_threadpool(
                send_whatsapp_image,
                phone,
                product_image["image_url"],
                product_image["caption"],
            )
            save_message(
                db,
                phone,
                f"[image] {product_image['caption']}",
                "outgoing",
            )
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="product_image_send_failed",
                    status="failed",
                    payload=json.dumps(
                        {
                            "title": product_image["title"],
                            "image_url": product_image["image_url"],
                        }
                    ),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            fallback_text = (
                "Image send nahi ho payi, lekin product detail yeh hai:\n"
                f"{product_image['caption']}"
            )
            await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
            save_message(db, phone, fallback_text, "outgoing")

        event.status = "processed"
        event.processed_at = datetime.utcnow()
        db.commit()
        return

    ai_reply = agent_state["reply_override"] or generate_ai_reply(
        db,
        phone,
        text,
        agent_context=agent_state["context"],
    )
    await run_in_threadpool(send_whatsapp_message, phone, ai_reply)
    save_message(db, phone, ai_reply, "outgoing")

    event.status = "processed"
    event.processed_at = datetime.utcnow()
    db.commit()


def mark_webhook_event_failed(db: Session, event: WebhookEvent, exc: Exception) -> None:
    event.status = "failed"
    event.error = str(exc)
    db.commit()


def serialize_ecommerce_connection(connection: EcommerceConnection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "platform": connection.platform,
        "store_url": connection.store_url,
        "status": connection.status,
        "has_access_token": bool(connection.access_token),
        "has_consumer_key": bool(connection.consumer_key),
        "has_consumer_secret": bool(connection.consumer_secret),
        "last_sync_at": str(connection.last_sync_at) if connection.last_sync_at else None,
        "created_at": str(connection.created_at),
    }


def serialize_ecommerce_order(order: EcommerceOrder) -> dict:
    try:
        items = json.loads(order.items or "[]")
    except json.JSONDecodeError:
        items = []

    return {
        "id": order.id,
        "connection_id": order.connection_id,
        "platform": order.platform,
        "external_id": order.external_id,
        "order_number": order.order_number,
        "phone": order.phone,
        "email": order.email,
        "customer_name": order.customer_name,
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "total": order.total,
        "currency": order.currency,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "items": items,
        "delivered_message_sent_at": (
            str(order.delivered_message_sent_at) if order.delivered_message_sent_at else None
        ),
        "updated_at": str(order.updated_at),
    }


def serialize_ecommerce_product(product: EcommerceProduct) -> dict:
    try:
        image_urls = json.loads(product.image_urls or "[]")
    except json.JSONDecodeError:
        image_urls = []

    return {
        "id": product.id,
        "connection_id": product.connection_id,
        "platform": product.platform,
        "external_id": product.external_id,
        "title": product.title,
        "handle": product.handle,
        "product_url": product.product_url,
        "description": product.description,
        "vendor": product.vendor,
        "product_type": product.product_type,
        "tags": product.tags,
        "status": product.status,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "currency": product.currency,
        "sku": product.sku,
        "inventory": product.inventory,
        "image_urls": image_urls,
        "updated_at": str(product.updated_at),
    }


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "AI WhatsApp Automation Backend Running",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/send-message")
async def send_message(
    data: SendMessageRequest,
    db: Session = Depends(get_db),
):
    try:
        response = await run_in_threadpool(
            send_whatsapp_message,
            data.phone,
            data.message,
        )
        save_message(db, data.phone, data.message, "outgoing")
        return {"status": "sent", "whatsapp": response}
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv("VERIFY_TOKEN")

    if mode == "subscribe" and token == verify_token and challenge:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    incoming_messages = parse_whatsapp_messages(body)
    processed = 0
    skipped = 0
    failed = 0
    errors = []

    for incoming in incoming_messages:
        event, created = get_or_create_webhook_event(db, incoming)
        if not should_process_webhook_event(event, created):
            skipped += 1
            continue

        try:
            await process_webhook_event(event, db)
            processed += 1
        except Exception as exc:
            mark_webhook_event_failed(db, event, exc)
            failed += 1
            errors.append({"event_id": event.id, "error": str(exc)})

    if failed:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "processed": processed,
                "failed": failed,
                "errors": errors[:5],
            },
        )

    return {
        "status": "ok",
        "received": len(incoming_messages),
        "processed": processed,
        "skipped": skipped,
    }


@app.get("/webhook/events")
def list_webhook_events(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(WebhookEvent)
    if status:
        query = query.filter(WebhookEvent.status == status)

    rows = query.order_by(WebhookEvent.created_at.desc()).limit(200).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "external_id": row.external_id,
            "phone": row.phone,
            "message_text": row.message_text,
            "status": row.status,
            "attempts": row.attempts,
            "error": row.error,
            "created_at": str(row.created_at),
            "processed_at": str(row.processed_at) if row.processed_at else None,
        }
        for row in rows
    ]


@app.post("/webhook/events/retry-failed")
async def retry_failed_webhook_events(
    data: RetryWebhookEventsRequest,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.status == "failed")
        .order_by(WebhookEvent.created_at.asc())
        .limit(max(1, min(data.limit, 100)))
        .all()
    )

    retried = 0
    failed = 0
    errors = []
    for event in rows:
        try:
            await process_webhook_event(event, db)
            retried += 1
        except Exception as exc:
            mark_webhook_event_failed(db, event, exc)
            failed += 1
            errors.append({"event_id": event.id, "error": str(exc)})

    return {
        "status": "completed",
        "retried": retried,
        "failed": failed,
        "errors": errors[:5],
    }


@app.post("/scrape")
async def scrape(data: ScrapeRequest, db: Session = Depends(get_db)):
    try:
        pages = await run_in_threadpool(crawl_website, data.url, data.max_pages)
        if not pages:
            raise HTTPException(status_code=502, detail="Scrape failed: no readable pages found")

        saved_pages = []
        total_chunks = 0
        pinecone_upserted = 0
        for page in pages:
            row = ScrapedData(url=page["url"], content=page["content"])
            db.add(row)
            db.commit()
            db.refresh(row)
            rag_result = save_scraped_chunks(db, row)
            total_chunks += rag_result["chunks"]
            pinecone_upserted += rag_result["pinecone_upserted"]
            saved_pages.append(
                {
                    "id": row.id,
                    "url": row.url,
                    "title": page.get("title"),
                    "content_length": len(row.content),
                    "images": len(page.get("image_urls") or []),
                    "social_links": len(page.get("social_links") or []),
                }
            )

        return {
            "status": "success",
            "requested_url": data.url,
            "pages_scraped": len(saved_pages),
            "chunk_count": total_chunks,
            "pinecone_upserted": pinecone_upserted,
            "pages": saved_pages,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {exc}") from exc


@app.get("/scraped-data")
def list_scraped_data(db: Session = Depends(get_db)):
    rows = db.query(ScrapedData).order_by(ScrapedData.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "url": row.url,
            "content_length": len(row.content),
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.post("/rag/rebuild")
def rebuild_rag_index(db: Session = Depends(get_db)):
    rows = db.query(ScrapedData).order_by(ScrapedData.created_at.asc()).all()
    documents = db.query(KnowledgeDocument).order_by(KnowledgeDocument.created_at.asc()).all()
    total_chunks = 0
    pinecone_upserted = 0

    for row in rows:
        result = save_scraped_chunks(db, row)
        total_chunks += result["chunks"]
        pinecone_upserted += result["pinecone_upserted"]
    for document in documents:
        result = save_knowledge_chunks(db, document)
        total_chunks += result["chunks"]
        pinecone_upserted += result["pinecone_upserted"]

    return {
        "status": "success",
        "scraped_documents": len(rows),
        "knowledge_documents": len(documents),
        "chunks": total_chunks,
        "pinecone_upserted": pinecone_upserted,
    }


@app.get("/rag/status")
def rag_status():
    return {
        "provider": "pinecone",
        "pinecone": pinecone_status(),
        "fallback": "local keyword/hash retrieval",
    }


@app.post("/knowledge/documents")
def add_knowledge_document(data: DocumentRequest, db: Session = Depends(get_db)):
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="Document content is required")

    document = save_knowledge_document(
        db,
        title=data.title,
        source=data.source,
        content=data.content,
    )
    return {
        "status": "success",
        "id": document.id,
        "title": document.title,
        "source": document.source,
        "content_length": len(document.content),
    }


@app.get("/knowledge/documents")
def list_knowledge_documents(db: Session = Depends(get_db)):
    rows = db.query(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "source": row.source,
            "content_length": len(row.content),
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.post("/ecommerce/connections")
def add_ecommerce_connection(
    data: EcommerceConnectionRequest,
    db: Session = Depends(get_db),
):
    try:
        connection = create_connection(
            db,
            name=data.name,
            platform=data.platform,
            store_url=data.store_url,
            access_token=data.access_token,
            consumer_key=data.consumer_key,
            consumer_secret=data.consumer_secret,
        )
        return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/ecommerce/connections")
def list_ecommerce_connections(db: Session = Depends(get_db)):
    rows = db.query(EcommerceConnection).order_by(EcommerceConnection.created_at.desc()).all()
    return [serialize_ecommerce_connection(row) for row in rows]


@app.patch("/ecommerce/connections/{connection_id}")
def patch_ecommerce_connection(
    connection_id: int,
    data: EcommerceConnectionUpdateRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        connection = update_connection(
            db,
            connection,
            name=data.name,
            store_url=data.store_url,
            access_token=data.access_token,
            consumer_key=data.consumer_key,
            consumer_secret=data.consumer_secret,
            status=data.status,
        )
        return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ecommerce/connections/{connection_id}/test")
async def check_ecommerce_connection(connection_id: int, db: Session = Depends(get_db)):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        return await run_in_threadpool(test_connection, connection)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ecommerce/connections/{connection_id}/sync-orders")
async def sync_ecommerce_orders(
    connection_id: int,
    data: EcommerceSyncRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        return await run_in_threadpool(sync_orders, db, connection, data.limit)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ecommerce/connections/{connection_id}/sync-products")
async def sync_ecommerce_products(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        result = await run_in_threadpool(sync_products, db, connection, data.limit)
        result.update(sync_product_catalog_knowledge(db, connection, data.limit))
        return result
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/ecommerce/products")
def list_ecommerce_products(
    connection_id: int | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(EcommerceProduct)
    if connection_id is not None:
        query = query.filter(EcommerceProduct.connection_id == connection_id)
    if q:
        search = f"%{q.strip()}%"
        query = query.filter(
            (EcommerceProduct.title.ilike(search))
            | (EcommerceProduct.description.ilike(search))
            | (EcommerceProduct.tags.ilike(search))
            | (EcommerceProduct.sku.ilike(search))
        )

    rows = query.order_by(EcommerceProduct.updated_at.desc()).limit(200).all()
    return [serialize_ecommerce_product(row) for row in rows]


@app.post("/ecommerce/sync-active")
async def sync_all_active_ecommerce_connections():
    return await run_in_threadpool(sync_active_ecommerce_connections)


@app.post("/ecommerce/connections/{connection_id}/webhook/order")
async def receive_ecommerce_order_webhook(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid ecommerce webhook JSON") from exc

    order_payload = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
    try:
        order = upsert_ecommerce_order(db, connection, order_payload)
    except Exception as exc:
        db.add(
            AgentAction(
                action_type="ecommerce_order_webhook_failed",
                status="failed",
                payload=json.dumps({"connection_id": connection.id}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "success",
        "order": serialize_ecommerce_order(order),
    }


@app.get("/ecommerce/orders")
def list_ecommerce_orders(
    platform: str | None = None,
    phone: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(EcommerceOrder)
    if platform:
        query = query.filter(EcommerceOrder.platform == platform.strip().lower())
    if phone:
        query = query.filter(EcommerceOrder.phone == phone)
    if status:
        query = query.filter(EcommerceOrder.status == status)

    rows = query.order_by(EcommerceOrder.updated_at.desc()).limit(200).all()
    return [serialize_ecommerce_order(row) for row in rows]


@app.get("/ecommerce/orders/{order_id}")
def get_ecommerce_order(order_id: str, db: Session = Depends(get_db)):
    normalized_order_id = order_id.strip().lstrip("#")
    row = (
        db.query(EcommerceOrder)
        .filter(
            (EcommerceOrder.order_number == order_id)
            | (EcommerceOrder.order_number == f"#{normalized_order_id}")
            | (EcommerceOrder.external_id == normalized_order_id)
        )
        .order_by(EcommerceOrder.updated_at.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Ecommerce order not found")
    return serialize_ecommerce_order(row)


@app.post("/ecommerce/automations/delivered-followups")
async def run_delivered_followups(
    data: DeliveredFollowupRequest,
    db: Session = Depends(get_db),
):
    return await run_in_threadpool(send_delivered_followups, db, data.limit)


@app.get("/leads")
def list_leads(db: Session = Depends(get_db)):
    rows = db.query(Lead).order_by(Lead.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "name": row.name,
            "email": row.email,
            "intent": row.intent,
            "status": row.status,
            "source": row.source,
            "notes": row.notes,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.get("/appointments")
def list_appointments(db: Session = Depends(get_db)):
    rows = db.query(Appointment).order_by(Appointment.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "customer_name": row.customer_name,
            "requested_time": row.requested_time,
            "status": row.status,
            "notes": row.notes,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.post("/orders")
def upsert_order(data: OrderRequest, db: Session = Depends(get_db)):
    order_id = data.order_id.strip().upper()
    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID is required")

    row = db.query(OrderStatus).filter(OrderStatus.order_id == order_id).first()
    if not row:
        row = OrderStatus(order_id=order_id)
        db.add(row)

    row.phone = data.phone or row.phone
    row.status = data.status
    row.details = data.details
    db.commit()
    db.refresh(row)

    return {
        "status": "success",
        "id": row.id,
        "order_id": row.order_id,
        "order_status": row.status,
    }


@app.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    rows = db.query(OrderStatus).order_by(OrderStatus.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "order_id": row.order_id,
            "status": row.status,
            "details": row.details,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.get("/handoffs")
def list_handoffs(db: Session = Depends(get_db)):
    rows = db.query(HandoffTicket).order_by(HandoffTicket.created_at.desc()).all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "reason": row.reason,
            "status": row.status,
            "summary": row.summary,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.post("/handoffs/{ticket_id}/close")
def close_handoff(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(HandoffTicket).filter(HandoffTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "closed"
    db.commit()
    return {"status": "success", "ticket_id": ticket.id}


@app.get("/customers/{phone}/memory")
def get_customer_memory(phone: str, db: Session = Depends(get_db)):
    rows = (
        db.query(CustomerMemory)
        .filter(CustomerMemory.phone == phone)
        .order_by(CustomerMemory.created_at.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "memory_type": row.memory_type,
            "content": row.content,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.post("/agent/actions/crm-update")
def crm_update(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_crm_update(db, data.phone, data.payload)
    return {"status": "logged", "action_id": action.id}


@app.post("/agent/actions/email")
def email_action(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_email_request(db, data.phone, data.payload)
    return {"status": "queued", "action_id": action.id}


@app.post("/agent/actions/payment-link")
def payment_link_action(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_payment_link_request(db, data.phone, data.payload)
    return {"status": "logged", "action_id": action.id}


@app.get("/agent/actions")
def list_agent_actions(db: Session = Depends(get_db)):
    rows = db.query(AgentAction).order_by(AgentAction.created_at.desc()).limit(100).all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "action_type": row.action_type,
            "status": row.status,
            "payload": row.payload,
            "result": row.result,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@app.get("/conversations")
def get_conversations(db: Session = Depends(get_db)):
    conversations = db.query(Message.phone).distinct().all()
    return [{"phone": conversation[0]} for conversation in conversations]


@app.get("/messages/{phone}")
def get_messages(phone: str, db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.phone == phone)
        .order_by(Message.created_at.asc())
        .all()
    )

    return [
        {
            "id": message.id,
            "phone": message.phone,
            "message": message.message,
            "direction": message.direction,
            "created_at": str(message.created_at),
        }
        for message in messages
    ]

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
