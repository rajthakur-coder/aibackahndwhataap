from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import CustomerMemory
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


def remember_value(db: Session, phone: str, memory_type: str, content: str, tenant_id: str | None = None) -> None:
    if not phone or not content:
        return
    tenant_id = normalize_tenant_id(tenant_id or current_tenant_id() or DEFAULT_TENANT_ID)

    memory = db.execute(
        select(CustomerMemory)
        .where(CustomerMemory.tenant_id == tenant_id, CustomerMemory.phone == phone, CustomerMemory.memory_type == memory_type)
        .order_by(CustomerMemory.created_at.desc())
    ).scalars().first()
    if memory:
        memory.content = content[:1000]
    else:
        db.add(CustomerMemory(tenant_id=tenant_id, phone=phone, memory_type=memory_type, content=content[:1000]))
    db.commit()


def remember_last_question(db: Session, phone: str, message: str, tenant_id: str | None = None) -> None:
    remember_value(db, phone, "last_question", message, tenant_id=tenant_id)


def remember_last_products(db: Session, phone: str, products: list[dict]) -> None:
    if not products:
        return
    summary = "; ".join(
        filter(
            None,
            [
                f"{product.get('title') or product.get('name')}"
                + (f" ({product.get('price_min')})" if product.get("price_min") else "")
                for product in products[:5]
            ],
        )
    )
    remember_value(db, phone, "last_products", summary)
