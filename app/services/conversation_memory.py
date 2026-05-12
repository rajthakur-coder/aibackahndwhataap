from sqlalchemy.orm import Session

from app.models.entities import CustomerMemory


def remember_value(db: Session, phone: str, memory_type: str, content: str) -> None:
    if not phone or not content:
        return

    memory = (
        db.query(CustomerMemory)
        .filter(CustomerMemory.phone == phone, CustomerMemory.memory_type == memory_type)
        .order_by(CustomerMemory.created_at.desc())
        .first()
    )
    if memory:
        memory.content = content[:1000]
    else:
        db.add(CustomerMemory(phone=phone, memory_type=memory_type, content=content[:1000]))
    db.commit()


def remember_last_question(db: Session, phone: str, message: str) -> None:
    remember_value(db, phone, "last_question", message)


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
