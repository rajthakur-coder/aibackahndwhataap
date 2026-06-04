from dataclasses import dataclass

from app.db.session import SessionLocal
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential, whatsapp_access_token
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


@dataclass(frozen=True)
class WhatsappClientCredentials:
    access_token: str
    phone_number_id: str


def resolve_whatsapp_client_credentials(tenant_id: str | None = None) -> WhatsappClientCredentials:
    resolved_tenant_id = normalize_tenant_id(tenant_id or current_tenant_id() or DEFAULT_TENANT_ID)

    with SessionLocal() as db:
        credential = get_whatsapp_credential(db, tenant_id=resolved_tenant_id)
        access_token = whatsapp_access_token(credential)
        phone_number_id = credential.phone_number_id if credential else None

    if access_token and phone_number_id:
        return WhatsappClientCredentials(
            access_token=access_token,
            phone_number_id=str(phone_number_id),
        )

    raise RuntimeError("WhatsApp credentials are not configured")


__all__ = ["WhatsappClientCredentials", "resolve_whatsapp_client_credentials"]
