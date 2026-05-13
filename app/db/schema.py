from sqlalchemy import inspect, text

from app.models.entities import (
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
    EcommerceProduct,
    ShopifyWebhookEvent,
)


ECOMMERCE_MODELS = [
    EcommerceConnection,
    EcommerceOrder,
    EcommerceProduct,
    EcommerceCustomer,
    ShopifyWebhookEvent,
]


def ensure_ecommerce_schema(engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for model in ECOMMERCE_MODELS:
            table = model.__table__
            if table.name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns or column.primary_key:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')
                )


def ensure_sqlite_ecommerce_schema(engine) -> None:
    ensure_ecommerce_schema(engine)
