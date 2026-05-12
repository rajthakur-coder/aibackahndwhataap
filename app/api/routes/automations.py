import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    AutomationEvent,
    AutomationExecution,
    AutomationRule,
    MessageTemplate,
)
from app.schemas import (
    AbandonedCartRequest,
    AutomationEventRequest,
    AutomationRuleRequest,
    AutomationRuleUpdateRequest,
    MessageTemplateRequest,
    SendTemplateRequest,
)
from app.services.automations import (
    bool_to_db,
    create_abandoned_cart_event,
    create_automation_event,
    ensure_default_automation_rules,
    process_automation_event,
    process_due_automation_events,
    render_template,
    serialize_event,
    serialize_execution,
    serialize_rule,
    serialize_template,
)
from app.services.whatsapp import send_whatsapp_message, send_whatsapp_template


router = APIRouter(prefix="/automations", tags=["automations"])


@router.post("/seed-defaults")
def seed_default_automations(db: Session = Depends(get_db)):
    return ensure_default_automation_rules(db)


@router.post("/templates")
def create_message_template(
    data: MessageTemplateRequest,
    db: Session = Depends(get_db),
):
    if not data.name.strip() or not data.body.strip():
        raise HTTPException(status_code=400, detail="Template name and body are required")
    exists = db.query(MessageTemplate).filter(MessageTemplate.name == data.name.strip()).first()
    if exists:
        raise HTTPException(status_code=409, detail="Template name already exists")

    template = MessageTemplate(
        name=data.name.strip(),
        body=data.body.strip(),
        channel=data.channel.strip() or "whatsapp",
        template_type=data.template_type.strip() or "text",
        provider_template_name=(
            data.provider_template_name.strip()
            if data.provider_template_name
            else None
        ),
        language=data.language.strip() or "en",
        body_variable_order=json.dumps(data.body_variable_order, ensure_ascii=True),
        status=data.status.strip() or "active",
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return {"status": "success", "template": serialize_template(template)}


@router.get("/templates")
def list_message_templates(db: Session = Depends(get_db)):
    rows = db.query(MessageTemplate).order_by(MessageTemplate.created_at.desc()).all()
    return [serialize_template(row) for row in rows]


@router.post("/templates/{template_id}/send-test")
def send_template_test(
    template_id: int,
    data: SendTemplateRequest,
    db: Session = Depends(get_db),
):
    template = db.query(MessageTemplate).filter(MessageTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Message template not found")
    if template.status != "active":
        raise HTTPException(status_code=400, detail="Message template is not active")

    rendered = render_template(template.body, data.context)
    if template.template_type == "whatsapp_template":
        variable_order = json.loads(template.body_variable_order or "[]")
        body_parameters = [
            str(data.context.get(variable, ""))
            for variable in variable_order
            if isinstance(variable, str)
        ]
        response = send_whatsapp_template(
            data.phone,
            template.provider_template_name or template.name,
            language=template.language or "en",
            body_parameters=body_parameters,
        )
    else:
        response = send_whatsapp_message(data.phone, rendered)

    return {
        "status": "sent",
        "rendered_message": rendered,
        "whatsapp": response,
    }


@router.post("/rules")
def create_automation_rule(
    data: AutomationRuleRequest,
    db: Session = Depends(get_db),
):
    if not data.name.strip() or not data.trigger.strip():
        raise HTTPException(status_code=400, detail="Rule name and trigger are required")
    if not data.message_template_id and not (data.message_body or "").strip():
        raise HTTPException(status_code=400, detail="Message template or message body is required")

    rule = AutomationRule(
        name=data.name.strip(),
        trigger=data.trigger.strip(),
        message_template_id=data.message_template_id,
        message_body=(data.message_body or "").strip() or None,
        delay_seconds=max(0, data.delay_seconds),
        conditions=json.dumps(data.conditions or {}, ensure_ascii=True),
        enabled=bool_to_db(data.enabled),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"status": "success", "rule": serialize_rule(rule)}


@router.get("/rules")
def list_automation_rules(
    trigger: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AutomationRule)
    if trigger:
        query = query.filter(AutomationRule.trigger == trigger.strip())
    rows = query.order_by(AutomationRule.created_at.desc()).all()
    return [serialize_rule(row) for row in rows]


@router.patch("/rules/{rule_id}")
def update_automation_rule(
    rule_id: int,
    data: AutomationRuleUpdateRequest,
    db: Session = Depends(get_db),
):
    rule = db.query(AutomationRule).filter(AutomationRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found")

    if data.name is not None:
        rule.name = data.name.strip() or rule.name
    if data.trigger is not None:
        rule.trigger = data.trigger.strip() or rule.trigger
    if data.message_template_id is not None:
        rule.message_template_id = data.message_template_id
    if data.message_body is not None:
        rule.message_body = data.message_body.strip() or None
    if data.delay_seconds is not None:
        rule.delay_seconds = max(0, data.delay_seconds)
    if data.conditions is not None:
        rule.conditions = json.dumps(data.conditions, ensure_ascii=True)
    if data.enabled is not None:
        rule.enabled = bool_to_db(data.enabled)

    db.commit()
    db.refresh(rule)
    return {"status": "success", "rule": serialize_rule(rule)}


@router.post("/events")
def add_automation_event(
    data: AutomationEventRequest,
    db: Session = Depends(get_db),
):
    event = create_automation_event(
        db,
        trigger=data.trigger,
        source=data.source,
        external_id=data.external_id,
        phone=data.phone,
        payload=data.payload,
        delay_seconds=data.delay_seconds,
    )
    return {"status": "queued", "event": serialize_event(event)}


@router.post("/events/abandoned-cart")
def add_abandoned_cart_event(
    data: AbandonedCartRequest,
    db: Session = Depends(get_db),
):
    payload = data.model_dump()
    event = create_abandoned_cart_event(
        db,
        payload=payload,
        source="api",
        delay_seconds=data.delay_seconds,
    )
    return {"status": "queued", "event": serialize_event(event)}


@router.get("/events")
def list_automation_events(
    status: str | None = None,
    trigger: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AutomationEvent)
    if status:
        query = query.filter(AutomationEvent.status == status.strip())
    if trigger:
        query = query.filter(AutomationEvent.trigger == trigger.strip())
    rows = query.order_by(AutomationEvent.created_at.desc()).limit(200).all()
    return [serialize_event(row) for row in rows]


@router.post("/events/{event_id}/process")
def process_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(AutomationEvent).filter(AutomationEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Automation event not found")
    return process_automation_event(db, event)


@router.post("/process-due")
def process_due(limit: int = 50, db: Session = Depends(get_db)):
    return process_due_automation_events(db, limit=limit)


@router.get("/executions")
def list_automation_executions(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AutomationExecution)
    if status:
        query = query.filter(AutomationExecution.status == status.strip())
    rows = query.order_by(AutomationExecution.created_at.desc()).limit(200).all()
    return [serialize_execution(row) for row in rows]
