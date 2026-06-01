"""observability dead letter and tenant guards

Revision ID: 20260526_0019
Revises: 20260526_0018
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0019"
down_revision: Union[str, None] = "20260526_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(table_name) and not _has_column(inspector, table_name, column.name):
        op.add_column(table_name, column)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return
    indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(), nullable=True),
            sa.Column("entity_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True, server_default="success"),
            sa.Column("request_id", sa.String(), nullable=True),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ["tenant_id", "user_id", "action", "entity_type", "entity_id", "status", "request_id"]:
            op.create_index(op.f(f"ix_audit_logs_{column}"), "audit_logs", [column], unique=False)

    webhook_columns = [
        sa.Column("tenant_id", sa.String(), nullable=True, server_default="default"),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(), nullable=True),
    ]
    for column in webhook_columns:
        _add_column_if_missing("webhook_events", column)
    _create_index_if_missing("ix_webhook_events_tenant_id", "webhook_events", ["tenant_id"])
    _create_index_if_missing("ix_webhook_events_request_id", "webhook_events", ["request_id"])
    _create_index_if_missing("ix_webhook_events_next_retry_at", "webhook_events", ["next_retry_at"])
    _create_index_if_missing("ix_webhook_events_dead_lettered_at", "webhook_events", ["dead_lettered_at"])

    shopify_columns = [
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(), nullable=True),
    ]
    for column in shopify_columns:
        _add_column_if_missing("shopify_webhook_events", column)
    _create_index_if_missing("ix_shopify_webhook_events_request_id", "shopify_webhook_events", ["request_id"])
    _create_index_if_missing("ix_shopify_webhook_events_next_retry_at", "shopify_webhook_events", ["next_retry_at"])
    _create_index_if_missing("ix_shopify_webhook_events_dead_lettered_at", "shopify_webhook_events", ["dead_lettered_at"])

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                CREATE OR REPLACE FUNCTION reject_default_tenant_id()
                RETURNS trigger AS $$
                BEGIN
                    IF NEW.tenant_id IS NULL OR btrim(NEW.tenant_id) = '' OR NEW.tenant_id = 'default' THEN
                        RAISE EXCEPTION 'tenant_id must be set to a real tenant for table %', TG_TABLE_NAME;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        protected_tables = [
            "users",
            "integrations",
            "whatsapp_credentials",
            "whatsapp_templates",
            "ecommerce_connections",
            "ecommerce_orders",
            "ecommerce_products",
            "ecommerce_customers",
            "contact_store_mappings",
            "shopify_catalog_collections",
            "shopify_catalog_default_categories",
            "shopify_webhook_events",
            "bot_settings",
            "knowledge_bases",
        ]
        for table_name in protected_tables:
            if inspector.has_table(table_name) and _has_column(inspector, table_name, "tenant_id"):
                bind.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_reject_default_tenant_id ON {table_name}"))
                bind.execute(
                    sa.text(
                        f"""
                        CREATE TRIGGER trg_reject_default_tenant_id
                        BEFORE INSERT OR UPDATE OF tenant_id ON {table_name}
                        FOR EACH ROW EXECUTE FUNCTION reject_default_tenant_id()
                        """
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        protected_tables = [
            "users",
            "integrations",
            "whatsapp_credentials",
            "whatsapp_templates",
            "ecommerce_connections",
            "ecommerce_orders",
            "ecommerce_products",
            "ecommerce_customers",
            "contact_store_mappings",
            "shopify_catalog_collections",
            "shopify_catalog_default_categories",
            "shopify_webhook_events",
            "bot_settings",
            "knowledge_bases",
        ]
        for table_name in protected_tables:
            if inspector.has_table(table_name):
                bind.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_reject_default_tenant_id ON {table_name}"))
        bind.execute(sa.text("DROP FUNCTION IF EXISTS reject_default_tenant_id()"))

    for index_name, table_name in [
        ("ix_shopify_webhook_events_dead_lettered_at", "shopify_webhook_events"),
        ("ix_shopify_webhook_events_next_retry_at", "shopify_webhook_events"),
        ("ix_shopify_webhook_events_request_id", "shopify_webhook_events"),
        ("ix_webhook_events_dead_lettered_at", "webhook_events"),
        ("ix_webhook_events_next_retry_at", "webhook_events"),
        ("ix_webhook_events_request_id", "webhook_events"),
        ("ix_webhook_events_tenant_id", "webhook_events"),
    ]:
        if inspector.has_table(table_name):
            indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            if index_name in indexes:
                op.drop_index(index_name, table_name=table_name)

    for table_name, columns in {
        "shopify_webhook_events": ["dead_lettered_at", "next_retry_at", "last_error", "request_id"],
        "webhook_events": ["dead_lettered_at", "next_retry_at", "last_error", "request_id", "tenant_id"],
    }.items():
        if inspector.has_table(table_name):
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name in columns:
                if column_name in existing_columns:
                    op.drop_column(table_name, column_name)

    if inspector.has_table("audit_logs"):
        op.drop_table("audit_logs")
