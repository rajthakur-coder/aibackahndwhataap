from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.audit import write_async_audit_log
from app.modules.compliance.compliance_schema import ConsentRequest, DataPrincipalRequest, DataPrincipalResolveRequest
from app.modules.compliance.compliance_service import (
    capture_consent,
    create_data_principal_request,
    delete_customer_data,
    dpdp_readiness,
    export_customer_data,
    latest_consent_status,
    list_data_principal_requests,
    resolve_data_principal_request,
)
from app.modules.compliance.security_audit_service import tenant_security_audit
from app.modules.compliance.tenant_isolation_audit_service import tenant_isolation_audit
from app.modules.compliance.template_compliance import check_template_compliance
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


compliance_router = APIRouter(prefix="/compliance", tags=["compliance"], dependencies=[Depends(get_current_user_token)])


@compliance_router.post("/consent")
async def consent(data: ConsentRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.run_sync(lambda sync_db: capture_consent(sync_db, tenant_id, data.phone, data.consent_type, data.status, data.purpose))
    await write_async_audit_log(db, action="compliance.consent_captured", tenant_id=tenant_id, entity_type="consent", entity_id=result["id"], metadata=result, commit=True)
    return result


@compliance_router.get("/consent/status")
async def consent_status(phone: str, consent_type: str = "marketing", tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: latest_consent_status(sync_db, tenant_id, phone, consent_type))


@compliance_router.get("/dpdp/readiness")
async def dpdp_status(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: dpdp_readiness(sync_db, tenant_id))


@compliance_router.post("/export")
async def export_data(data: DataPrincipalRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    request_row = await db.run_sync(lambda sync_db: create_data_principal_request(sync_db, tenant_id, data.phone, "export", data.requester_email, data.purpose))
    result = await db.run_sync(lambda sync_db: export_customer_data(sync_db, tenant_id, data.phone))
    await db.run_sync(lambda sync_db: resolve_data_principal_request(sync_db, tenant_id, request_row["id"], "completed", "Export generated"))
    await write_async_audit_log(db, action="compliance.data_exported", tenant_id=tenant_id, entity_type="customer", entity_id=data.phone, commit=True)
    return result


@compliance_router.post("/delete")
async def delete_data(data: DataPrincipalRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    request_row = await db.run_sync(lambda sync_db: create_data_principal_request(sync_db, tenant_id, data.phone, "delete", data.requester_email, data.purpose))
    result = await db.run_sync(lambda sync_db: delete_customer_data(sync_db, tenant_id, data.phone))
    await db.run_sync(lambda sync_db: resolve_data_principal_request(sync_db, tenant_id, request_row["id"], "completed", "Customer data deleted from scoped stores"))
    await write_async_audit_log(db, action="compliance.data_deleted", tenant_id=tenant_id, entity_type="customer", entity_id=data.phone, metadata=result["deleted"], commit=True)
    return result


@compliance_router.post("/data-principal/requests")
async def create_dsr(data: DataPrincipalRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await db.run_sync(lambda sync_db: create_data_principal_request(sync_db, tenant_id, data.phone, data.request_type, data.requester_email, data.purpose))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_async_audit_log(db, action="compliance.dsr_created", tenant_id=tenant_id, entity_type="data_principal_request", entity_id=result["id"], metadata=result, commit=True)
    return result


@compliance_router.get("/data-principal/requests")
async def list_dsr(status: str | None = None, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: list_data_principal_requests(sync_db, tenant_id, status))


@compliance_router.post("/data-principal/resolve")
async def resolve_dsr(data: DataPrincipalResolveRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await db.run_sync(lambda sync_db: resolve_data_principal_request(sync_db, tenant_id, data.request_id, data.status, data.result_summary))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await write_async_audit_log(db, action="compliance.dsr_resolved", tenant_id=tenant_id, entity_type="data_principal_request", entity_id=data.request_id, metadata=result, commit=True)
    return result


@compliance_router.get("/security/audit")
async def security_audit(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: tenant_security_audit(sync_db, tenant_id))


@compliance_router.get("/tenant-isolation/audit")
async def isolation_audit(_tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(tenant_isolation_audit)


@compliance_router.post("/template/check")
async def template_check(request: Request, _tenant_id: str = Depends(strict_tenant_id)):
    return check_template_compliance(await request.json())
