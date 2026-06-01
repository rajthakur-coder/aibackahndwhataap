import re
from contextvars import ContextVar, Token

from fastapi import Header, HTTPException, Request, status


DEFAULT_TENANT_ID = "default"
MAX_TENANT_ID_LENGTH = 80
TENANT_ID_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")

_tenant_context: ContextVar[str | None] = ContextVar("tenant_context", default=None)


def normalize_tenant_id(value: str | None) -> str:
    if not value:
        return DEFAULT_TENANT_ID

    tenant_id = TENANT_ID_RE.sub("", value.strip())
    return tenant_id[:MAX_TENANT_ID_LENGTH] or DEFAULT_TENANT_ID


def set_current_tenant_id(tenant_id: str | None) -> Token:
    return _tenant_context.set(normalize_tenant_id(tenant_id) if tenant_id else None)


def current_tenant_id() -> str | None:
    return _tenant_context.get()


def reset_current_tenant_id(token: Token) -> None:
    _tenant_context.reset(token)


def _require_context_tenant() -> str:
    tenant_id = current_tenant_id()
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (No tenant found)",
        )
    return tenant_id


def _validate_header_tenant(
    context_tenant_id: str,
    x_tenant_id: str | None,
) -> str:
    if not x_tenant_id:
        return context_tenant_id

    header_tenant_id = normalize_tenant_id(x_tenant_id)
    if header_tenant_id != context_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant mismatch",
        )

    return header_tenant_id


def tenant_id_from_header(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> str:
    context_tenant_id = _require_context_tenant()
    tenant_id = _validate_header_tenant(context_tenant_id, x_tenant_id)

    if tenant_id != context_tenant_id:
        set_current_tenant_id(tenant_id)

    return tenant_id


def strict_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> str:
    context_tenant_id = _require_context_tenant()
    return _validate_header_tenant(context_tenant_id, x_tenant_id)
