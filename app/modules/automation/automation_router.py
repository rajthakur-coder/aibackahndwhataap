from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_db as get_db
from app.modules.automation import automation_service as service
from app.modules.automation.automation_schema import (
    AbandonedCartRequest,
    AutomationEventRequest,
    AutomationRuleRequest,
    AutomationRuleUpdateRequest,
    MessageTemplateRequest,
    SendTemplateRequest,
)


router = APIRouter(prefix="/automations", tags=["automations"])


def _raise_if_error(result: dict) -> dict:
    if "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result["error"],
        )
    return result


@router.post("/seed-defaults")
async def seed_default_automations(db: AsyncSession = Depends(get_db)):
    return await service.seed_default_automations(db)


@router.post("/templates")
async def create_message_template(
    data: MessageTemplateRequest,
    db: AsyncSession = Depends(get_db),
):
    return _raise_if_error(await service.create_message_template(db, data))


@router.get("/templates")
async def list_message_templates(db: AsyncSession = Depends(get_db)):
    return await service.list_message_templates(db)


@router.post("/templates/{template_id}/send-test")
async def send_template_test(
    template_id: int,
    data: SendTemplateRequest,
    db: AsyncSession = Depends(get_db),
):
    return _raise_if_error(await service.send_template_test(db, template_id, data))


@router.post("/rules")
async def create_automation_rule(
    data: AutomationRuleRequest,
    db: AsyncSession = Depends(get_db),
):
    return _raise_if_error(await service.create_automation_rule(db, data))


@router.get("/rules")
async def list_automation_rules(
    trigger: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await service.list_automation_rules(db, trigger)


@router.patch("/rules/{rule_id}")
async def update_automation_rule(
    rule_id: int,
    data: AutomationRuleUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    return _raise_if_error(await service.update_automation_rule(db, rule_id, data))


@router.post("/events")
async def add_automation_event(
    data: AutomationEventRequest,
    db: AsyncSession = Depends(get_db),
):
    return await service.create_automation_event(db, data)


@router.post("/events/abandoned-cart")
async def add_abandoned_cart_event(
    data: AbandonedCartRequest,
    db: AsyncSession = Depends(get_db),
):
    return await service.create_abandoned_cart_event(db, data)


@router.get("/events")
async def list_automation_events(
    status: str | None = None,
    trigger: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await service.list_automation_events(db, status=status, trigger=trigger)


@router.post("/events/{event_id}/process")
async def process_event(event_id: int, db: AsyncSession = Depends(get_db)):
    return _raise_if_error(await service.process_event(db, event_id))


@router.post("/process-due")
async def process_due(limit: int = 50, db: AsyncSession = Depends(get_db)):
    return await service.process_due_events(db, limit=limit)


@router.get("/executions")
async def list_automation_executions(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await service.list_automation_executions(db, status=status)
