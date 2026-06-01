"""add standard integrations table

Revision ID: 20260526_0017
Revises: 20260526_0016
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0017"
down_revision: Union[str, None] = "20260526_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if not inspector.has_table("integrations"):
        op.create_table(
            "integrations",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="CONNECTED"),
            sa.Column("scopes", sa.Text(), nullable=True),
            sa.Column("access_token", sa.Text(), nullable=True),
            sa.Column("refresh_token", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_account_id", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_integrations_id"), "integrations", ["id"], unique=False)
        op.create_index(op.f("ix_integrations_provider"), "integrations", ["provider"], unique=False)
        op.create_index(op.f("ix_integrations_provider_account_id"), "integrations", ["provider_account_id"], unique=False)
        op.create_index(op.f("ix_integrations_status"), "integrations", ["status"], unique=False)
        op.create_index(op.f("ix_integrations_tenant_id"), "integrations", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_integrations_user_id"), "integrations", ["user_id"], unique=False)

    _add_uuid_column_if_missing(inspector, "whatsapp_credentials", "integration_id")
    _add_uuid_column_if_missing(inspector, "ecommerce_connections", "integration_id")

    # Backfill lightweight common rows for existing platform-specific connections.
    op.execute(
        """
        INSERT INTO integrations (
            id, user_id, tenant_id, provider, status, scopes, access_token,
            refresh_token, provider_account_id, display_name, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            users.id,
            whatsapp_credentials.tenant_id,
            'WHATSAPP_BUSINESS',
            CASE WHEN whatsapp_credentials.status = 'active' THEN 'CONNECTED' ELSE 'NEEDS_REAUTH' END,
            '["whatsapp_business_management","whatsapp_business_messaging","business_management"]',
            whatsapp_credentials.token,
            NULL,
            whatsapp_credentials.waba_id,
            COALESCE(whatsapp_credentials.business_name, whatsapp_credentials.verified_name, whatsapp_credentials.name),
            whatsapp_credentials.created_at,
            whatsapp_credentials.updated_at
        FROM whatsapp_credentials
        LEFT JOIN users ON users.tenant_id = whatsapp_credentials.tenant_id
        WHERE whatsapp_credentials.integration_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE whatsapp_credentials
        SET integration_id = integrations.id
        FROM integrations
        WHERE whatsapp_credentials.integration_id IS NULL
          AND integrations.tenant_id = whatsapp_credentials.tenant_id
          AND integrations.provider = 'WHATSAPP_BUSINESS'
          AND integrations.provider_account_id IS NOT DISTINCT FROM whatsapp_credentials.waba_id
        """
    )
    op.execute(
        """
        INSERT INTO integrations (
            id, user_id, tenant_id, provider, status, scopes, access_token,
            refresh_token, provider_account_id, display_name, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            users.id,
            ecommerce_connections.tenant_id,
            CASE WHEN ecommerce_connections.platform = 'shopify' THEN 'SHOPIFY' ELSE 'WOOCOMMERCE' END,
            CASE WHEN ecommerce_connections.status = 'active' THEN 'CONNECTED' ELSE 'NEEDS_REAUTH' END,
            CASE
                WHEN ecommerce_connections.platform = 'shopify'
                THEN '["read_products","read_inventory","read_orders","read_customers","read_checkouts","read_fulfillments","read_locations"]'
                ELSE '["read_products","read_orders","read_customers","write_webhooks"]'
            END,
            COALESCE(ecommerce_connections.encrypted_access_token, ecommerce_connections.access_token, ecommerce_connections.consumer_key),
            ecommerce_connections.consumer_secret,
            ecommerce_connections.store_url,
            ecommerce_connections.name,
            ecommerce_connections.created_at,
            ecommerce_connections.updated_at
        FROM ecommerce_connections
        LEFT JOIN users ON users.tenant_id = ecommerce_connections.tenant_id
        WHERE ecommerce_connections.integration_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE ecommerce_connections
        SET integration_id = integrations.id
        FROM integrations
        WHERE ecommerce_connections.integration_id IS NULL
          AND integrations.tenant_id = ecommerce_connections.tenant_id
          AND integrations.provider = CASE WHEN ecommerce_connections.platform = 'shopify' THEN 'SHOPIFY' ELSE 'WOOCOMMERCE' END
          AND integrations.provider_account_id = ecommerce_connections.store_url
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("ecommerce_connections"):
        columns = {column["name"] for column in inspector.get_columns("ecommerce_connections")}
        if "integration_id" in columns:
            with op.batch_alter_table("ecommerce_connections") as batch_op:
                batch_op.drop_column("integration_id")

    if inspector.has_table("whatsapp_credentials"):
        columns = {column["name"] for column in inspector.get_columns("whatsapp_credentials")}
        if "integration_id" in columns:
            with op.batch_alter_table("whatsapp_credentials") as batch_op:
                batch_op.drop_column("integration_id")

    if inspector.has_table("integrations"):
        op.drop_index(op.f("ix_integrations_user_id"), table_name="integrations")
        op.drop_index(op.f("ix_integrations_tenant_id"), table_name="integrations")
        op.drop_index(op.f("ix_integrations_status"), table_name="integrations")
        op.drop_index(op.f("ix_integrations_provider_account_id"), table_name="integrations")
        op.drop_index(op.f("ix_integrations_provider"), table_name="integrations")
        op.drop_index(op.f("ix_integrations_id"), table_name="integrations")
        op.drop_table("integrations")


def _add_uuid_column_if_missing(inspector, table_name: str, column_name: str) -> None:
    if not inspector.has_table(table_name):
        return
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(sa.Column(column_name, sa.Uuid(), nullable=True))
    op.create_index(op.f(f"ix_{table_name}_{column_name}"), table_name, [column_name], unique=False)
