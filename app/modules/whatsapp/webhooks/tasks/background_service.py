import asyncio
import json
import threading
from concurrent.futures import TimeoutError, ThreadPoolExecutor

from app.db.session import AsyncSessionLocal
from app.models.crm import AgentAction
from app.models.whatsapp import WebhookEvent
from app.modules.crm.memory.conversation_memory_service import remember_last_question
from app.modules.whatsapp.analytics.analytics_service import log_interactive_click
from app.modules.whatsapp.client.client_service import mark_whatsapp_message_read_with_typing


_BACKGROUND_LOG_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whatsapp-log")


async def _run_sync_db(sync_op) -> None:
    async with AsyncSessionLocal() as db:
        await db.run_sync(sync_op)


def _run_sync_db_in_thread(sync_op) -> None:
    asyncio.run(_run_sync_db(sync_op))


def start_remember_last_question(phone: str, text: str, tenant_id: str | None = None) -> None:
    if not phone or not text:
        return
    thread = threading.Thread(
        target=_remember_last_question_worker,
        args=(phone, text, tenant_id),
        daemon=True,
    )
    thread.start()


def _remember_last_question_worker(phone: str, text: str, tenant_id: str | None) -> None:
    def sync_op(db):
        try:
            remember_last_question(db, phone, text, tenant_id=tenant_id)
        except Exception as exc:
            db.add(
                AgentAction(
                    tenant_id=tenant_id,
                    phone=phone,
                    action_type="memory_save_failed",
                    status="failed",
                    payload=json.dumps({"memory_type": "last_question"}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
    _run_sync_db_in_thread(sync_op)


def start_log_interactive_click(phone: str, message_id: str | None, payload_text: str | None, tenant_id: str | None = None) -> None:
    if not phone or not payload_text:
        return
    _BACKGROUND_LOG_EXECUTOR.submit(_log_interactive_click_worker, phone, message_id, payload_text, tenant_id)


def _log_interactive_click_worker(phone: str, message_id: str | None, payload_text: str | None, tenant_id: str | None) -> None:
    def sync_op(db):
        try:
            payload = json.loads(payload_text or "{}")
            log_interactive_click(db, phone, message_id, payload, tenant_id=tenant_id)
            db.commit()
        except (TypeError, json.JSONDecodeError):
            return
        except Exception as exc:
            db.rollback()
            db.add(
                AgentAction(
                    tenant_id=tenant_id,
                    phone=phone,
                    action_type="analytics_log_failed",
                    status="failed",
                    payload=json.dumps({"message_id": message_id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
    _run_sync_db_in_thread(sync_op)


def start_log_query_understanding(phone: str, payload: dict) -> None:
    if not phone:
        return
    _BACKGROUND_LOG_EXECUTOR.submit(_log_query_understanding_worker, phone, payload)


def _log_query_understanding_worker(phone: str, payload: dict) -> None:
    def sync_op(db):
        try:
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="query_understanding",
                    status="logged",
                    payload=json.dumps(payload),
                )
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="query_understanding_log_failed",
                    status="failed",
                    payload=json.dumps(
                        {
                            "message": payload.get("message"),
                            "intent": payload.get("intent"),
                        }
                    ),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
    _run_sync_db_in_thread(sync_op)


def start_mark_read_with_typing(event: WebhookEvent, wait_seconds: float = 0.8) -> None:
    if not event.external_id:
        return
    future = _BACKGROUND_LOG_EXECUTOR.submit(_mark_read_with_typing_worker, event.external_id, event.phone)
    try:
        future.result(timeout=wait_seconds)
    except TimeoutError:
        return


def _mark_read_with_typing_worker(message_id: str, phone: str | None) -> None:
    try:
        mark_whatsapp_message_read_with_typing(message_id)
    except Exception as exc:
        def sync_op(db):
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="typing_indicator_failed",
                    status="failed",
                    payload=json.dumps({"message_id": message_id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
        _run_sync_db_in_thread(sync_op)
