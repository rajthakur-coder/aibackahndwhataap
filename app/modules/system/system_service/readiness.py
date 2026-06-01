from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.modules.automation.outbound_template_service import meta_template_approval_status
from app.modules.tenants.tenant_service import get_tenant_config, seed_tenant_config, serialize_tenant_config


REQUIRED_ENV = [
    "DATABASE_URI",
    "REDIS_URL",
    "SECRET_KEY",
    "OPENROUTER_API_KEY",
    "ACCESS_TOKEN",
    "PHONE_NUMBER_ID",
    "VERIFY_TOKEN",
    "ECOMMERCE_TOKEN_SECRET",
]


async def readiness_status(tenant_id: str = "default") -> dict:
    checks = {
        "database": await _database_check(),
        "environment": _environment_check(),
        "tenant_config": await _tenant_config_check(tenant_id),
        "meta_templates": await _meta_template_check(tenant_id),
        "automation": _automation_check(),
        "security": _security_check(),
    }
    ready = all(item.get("ok") for item in checks.values())
    return {
        "status": "ready" if ready else "not_ready",
        "tenant_id": tenant_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


async def ensure_live_tenant_config(tenant_id: str = "default") -> dict:
    async with AsyncSessionLocal() as db:
        def sync_op(sync_db):
            row = seed_tenant_config(sync_db, tenant_id=tenant_id, template="commerce", overwrite=False)
            return {"status": "ok", "config": serialize_tenant_config(row)}

        return await db.run_sync(sync_op)


async def _database_check() -> dict:
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("select 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _environment_check() -> dict:
    missing = [name for name in REQUIRED_ENV if not getattr(settings, name, None)]
    optional_missing = [
        name
        for name in ("SHIPROCKET_TOKEN", "SLACK_WEBHOOK_URL", "GMAIL_ID", "GMAIL_APP_PASSWORD")
        if not getattr(settings, name, None)
    ]
    return {"ok": not missing, "missing": missing, "optional_missing": optional_missing}


async def _tenant_config_check(tenant_id: str) -> dict:
    try:
        async with AsyncSessionLocal() as db:
            row = await db.run_sync(lambda sync_db: get_tenant_config(sync_db, tenant_id))
            if not row:
                return {"ok": False, "reason": "tenant_config_missing"}
            data = serialize_tenant_config(row)
            required = ["brand_name", "return_policy", "shipping_policy", "support_email"]
            missing = [key for key in required if not data.get(key)]
            return {"ok": not missing, "missing": missing, "brand_name": data.get("brand_name")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _meta_template_check(tenant_id: str) -> dict:
    try:
        async with AsyncSessionLocal() as db:
            status = await db.run_sync(lambda sync_db: meta_template_approval_status(sync_db, tenant_id))
            return {"ok": status.get("ready"), **status}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _automation_check() -> dict:
    return {
        "ok": bool(settings.AUTOMATION_PROCESSOR_ENABLED),
        "processor_enabled": settings.AUTOMATION_PROCESSOR_ENABLED,
        "interval_seconds": settings.AUTOMATION_PROCESSOR_INTERVAL_SECONDS,
    }


def _security_check() -> dict:
    warnings = []
    if settings.DEBUG:
        warnings.append("DEBUG is enabled")
    if not settings.COOKIE_SECURE:
        warnings.append("COOKIE_SECURE is disabled")
    if "*" in settings.CORS_ORIGINS:
        warnings.append("CORS allows all origins")
    return {"ok": not warnings, "warnings": warnings}
