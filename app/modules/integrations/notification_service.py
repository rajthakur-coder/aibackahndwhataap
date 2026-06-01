import json
import smtplib
from email.mime.text import MIMEText

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm import AgentAction
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config
from app.shared.tenant import normalize_tenant_id


REQUEST_TIMEOUT = 10


def notify_support_ticket(
    db: Session,
    *,
    tenant_id: str,
    phone: str,
    issue: str,
    summary: str,
    ticket_id: int,
    email: str | None = None,
) -> None:
    tenant = _tenant_payload(db, tenant_id)
    recipient = tenant.get("support_email")
    subject = f"WhatsApp support ticket #{ticket_id}"
    body = "\n".join(
        [
            f"Ticket: #{ticket_id}",
            f"Tenant: {tenant.get('brand_name') or normalize_tenant_id(tenant_id)}",
            f"Phone: {phone}",
            f"Customer email: {email or '-'}",
            "",
            "Issue:",
            issue,
            "",
            "Conversation summary:",
            summary,
        ]
    )
    _send_email_if_configured(db, tenant_id, phone, subject, recipient, body, "support_ticket_email")
    _send_slack_if_configured(db, tenant_id, phone, subject, body, "support_ticket_slack")


def notify_bulk_lead(
    db: Session,
    *,
    tenant_id: str,
    phone: str,
    lead_id: int,
    payload: dict,
    email: str | None = None,
) -> None:
    tenant = _tenant_payload(db, tenant_id)
    metadata = tenant.get("metadata") or {}
    recipient = metadata.get("bulk_lead_email") or tenant.get("support_email")
    subject = f"WhatsApp bulk lead #{lead_id}"
    body = "\n".join(
        [
            f"Lead: #{lead_id}",
            f"Tenant: {tenant.get('brand_name') or normalize_tenant_id(tenant_id)}",
            f"Phone: {phone}",
            f"Email: {email or '-'}",
            "",
            "Details:",
            json.dumps(payload, ensure_ascii=True, indent=2, default=str),
        ]
    )
    _send_email_if_configured(db, tenant_id, phone, subject, recipient, body, "bulk_lead_email")
    _send_slack_if_configured(db, tenant_id, phone, subject, body, "bulk_lead_slack")


def _tenant_payload(db: Session, tenant_id: str) -> dict:
    row = get_tenant_config(db, tenant_id)
    if not row:
        return {"tenant_id": normalize_tenant_id(tenant_id), "metadata": {}}
    return serialize_tenant_config(row)


def _send_email_if_configured(
    db: Session,
    tenant_id: str,
    phone: str,
    subject: str,
    recipient: str,
    body: str,
    action_type: str,
) -> None:
    sender = getattr(settings, "GMAIL_ID", None)
    password = getattr(settings, "GMAIL_APP_PASSWORD", None)
    if not sender or not password or not recipient:
        _log_notification(db, tenant_id, phone, action_type, "skipped", {"reason": "email_not_configured", "recipient": recipient})
        return
    try:
        message = MIMEText(body, "plain")
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, [recipient], message.as_string())
        _log_notification(db, tenant_id, phone, action_type, "sent", {"recipient": recipient})
    except Exception as exc:
        _log_notification(db, tenant_id, phone, action_type, "failed", {"recipient": recipient, "error": str(exc)})


def _send_slack_if_configured(
    db: Session,
    tenant_id: str,
    phone: str,
    subject: str,
    body: str,
    action_type: str,
) -> None:
    tenant = _tenant_payload(db, tenant_id)
    metadata = tenant.get("metadata") or {}
    webhook_url = metadata.get("slack_webhook_url") or getattr(settings, "SLACK_WEBHOOK_URL", None)
    if not webhook_url:
        _log_notification(db, tenant_id, phone, action_type, "skipped", {"reason": "slack_not_configured"})
        return
    try:
        requests.post(webhook_url, json={"text": f"*{subject}*\n```{body[:2500]}```"}, timeout=REQUEST_TIMEOUT).raise_for_status()
        _log_notification(db, tenant_id, phone, action_type, "sent", {})
    except Exception as exc:
        _log_notification(db, tenant_id, phone, action_type, "failed", {"error": str(exc)})


def _log_notification(db: Session, tenant_id: str, phone: str, action_type: str, status: str, result: dict) -> None:
    try:
        db.add(
            AgentAction(
                phone=phone,
                action_type=action_type,
                status=status,
                payload=json.dumps({"tenant_id": normalize_tenant_id(tenant_id)}, ensure_ascii=True),
                result=json.dumps(result, ensure_ascii=True, default=str),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
