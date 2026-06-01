import json
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.db.session import AsyncSessionLocal
from app.models.audit import AuditLog
from app.shared.rate_limit import check_rate_limit, limit_for_method
from app.shared.tenant import current_tenant_id, reset_current_tenant_id, set_current_tenant_id
from app.utils import decode_token


EXEMPT_PATH_PREFIXES = (
    "/webhook",
    "/webhooks/shopify",
    "/whatsapp/cloud-api/callback",
    "/health",
)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_context(request, call_next):
        tenant_token = set_current_tenant_id(None)
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            await audit_mutating_request(request, response.status_code, request_id)
            return response
        finally:
            reset_current_tenant_id(tenant_token)

    @app.middleware("http")
    async def production_rate_limit(request, call_next):
        path = request.url.path
        if is_exempt_path(path):
            return await call_next(request)

        ip = get_client_ip(request)
        tenant_hint = request.headers.get("X-Tenant-Id") or "anonymous"
        key = f"{tenant_hint}:{ip}:{path}"

        ok, remaining = check_rate_limit(key, limit=limit_for_method(request.method))
        if not ok:
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"X-RateLimit-Remaining": "0", "Retry-After": "60"},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


def is_exempt_path(path: str) -> bool:
    return path.startswith(EXEMPT_PATH_PREFIXES)


def get_client_ip(request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


async def audit_mutating_request(request, status_code: int, request_id: str) -> None:
    method = request.method
    path = request.url.path
    if method not in MUTATING_METHODS or is_exempt_path(path):
        return

    payload = decode_token(request.cookies.get("access_token") or "")
    user_id = str(payload.get("id")) if payload and payload.get("id") else None
    status = "success" if status_code < 400 else "failed"

    try:
        async with AsyncSessionLocal() as db:
            db.add(
                AuditLog(
                    tenant_id=current_tenant_id(),
                    user_id=user_id,
                    action="api.request",
                    entity_type="http_request",
                    entity_id=f"{method} {path}",
                    status=status,
                    request_id=request_id,
                    ip_address=get_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    metadata_json=json.dumps(
                        {"method": method, "path": path, "status_code": status_code},
                        ensure_ascii=True,
                    ),
                )
            )
            await db.commit()
    except Exception:
        return
