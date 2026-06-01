import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def write_audit_log(
    db: Session,
    *,
    action: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    status: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        status=status,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=json.dumps(metadata or {}),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


async def write_async_audit_log(
    db: AsyncSession,
    *,
    action: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    status: str = "success",
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        status=status,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=json.dumps(metadata or {}),
    )
    db.add(row)
    if commit:
        await db.commit()
        await db.refresh(row)
    return row
