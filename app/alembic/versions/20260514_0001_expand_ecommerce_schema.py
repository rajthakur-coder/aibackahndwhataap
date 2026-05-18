"""expand ecommerce schema for shopify automation

Revision ID: 20260514_0001
Revises:
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260514_0001"
down_revision = None
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    connection_columns = _columns("ecommerce_connections")
    if connection_columns:
        for column in [
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("store_name", sa.String(), nullable=True),
            sa.Column("myshopify_domain", sa.String(), nullable=True),
            sa.Column("encrypted_access_token", sa.Text(), nullable=True),
            sa.Column("shopify_shop_id", sa.String(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("timezone", sa.String(), nullable=True),
            sa.Column("owner_email", sa.String(), nullable=True),
            sa.Column("owner_phone", sa.String(), nullable=True),
            sa.Column("plan_name", sa.String(), nullable=True),
            sa.Column("webhook_status", sa.String(), nullable=True),
            sa.Column("installed_at", sa.DateTime(), nullable=True),
        ]:
            _add_column("ecommerce_connections", column)

    order_columns = _columns("ecommerce_orders")
    if order_columns:
        for column in [
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("shopify_order_id", sa.String(), nullable=True),
            sa.Column("ecommerce_customer_id", sa.Integer(), nullable=True),
            sa.Column("tags", sa.Text(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("shipping_address", sa.Text(), nullable=True),
            sa.Column("billing_address", sa.Text(), nullable=True),
            sa.Column("subtotal", sa.String(), nullable=True),
            sa.Column("discounts", sa.String(), nullable=True),
            sa.Column("tax", sa.String(), nullable=True),
            sa.Column("payment_gateway", sa.String(), nullable=True),
            sa.Column("skus", sa.Text(), nullable=True),
            sa.Column("product_ids", sa.Text(), nullable=True),
            sa.Column("tracking_numbers", sa.Text(), nullable=True),
            sa.Column("tracking_urls", sa.Text(), nullable=True),
            sa.Column("courier_company", sa.String(), nullable=True),
            sa.Column("shipment_status", sa.String(), nullable=True),
            sa.Column("delivery_status", sa.String(), nullable=True),
            sa.Column("shopify_created_at", sa.String(), nullable=True),
            sa.Column("shopify_updated_at", sa.String(), nullable=True),
        ]:
            _add_column("ecommerce_orders", column)

    product_columns = _columns("ecommerce_products")
    if product_columns:
        for column in [
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("shopify_product_id", sa.String(), nullable=True),
            sa.Column("description_html", sa.Text(), nullable=True),
            sa.Column("collections", sa.Text(), nullable=True),
            sa.Column("prices", sa.Text(), nullable=True),
            sa.Column("compare_at_prices", sa.Text(), nullable=True),
            sa.Column("skus", sa.Text(), nullable=True),
            sa.Column("variants", sa.Text(), nullable=True),
            sa.Column("options", sa.Text(), nullable=True),
            sa.Column("seo_title", sa.String(), nullable=True),
            sa.Column("seo_description", sa.Text(), nullable=True),
        ]:
            _add_column("ecommerce_products", column)

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "ecommerce_customers" not in tables:
        op.create_table(
            "ecommerce_customers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("connection_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("external_id", sa.String(), nullable=False),
            sa.Column("shopify_customer_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("total_orders", sa.Integer(), nullable=True),
            sa.Column("total_spend", sa.String(), nullable=True),
            sa.Column("tags", sa.Text(), nullable=True),
            sa.Column("addresses", sa.Text(), nullable=True),
            sa.Column("last_order_at", sa.String(), nullable=True),
            sa.Column("marketing_consent", sa.String(), nullable=True),
            sa.Column("preferred_language", sa.String(), nullable=True),
            sa.Column("whatsapp_opt_in", sa.String(), nullable=True),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if "shopify_webhook_events" not in tables:
        op.create_table(
            "shopify_webhook_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("connection_id", sa.Integer(), nullable=True),
            sa.Column("shop_domain", sa.String(), nullable=False),
            sa.Column("topic", sa.String(), nullable=False),
            sa.Column("webhook_id", sa.String(), nullable=True),
            sa.Column("payload_hash", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    pass
