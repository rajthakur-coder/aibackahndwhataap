"""create commerce action tables

Revision ID: 20260528_0026
Revises: 20260528_0025
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0026"
down_revision: Union[str, None] = "20260528_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = ("ecommerce_carts", "ecommerce_return_requests", "leads")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("ecommerce_carts"):
        op.create_table(
            "ecommerce_carts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("items", sa.Text(), nullable=True),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("subtotal", sa.String(), nullable=True),
            sa.Column("checkout_url", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_ecommerce_carts_id"), "ecommerce_carts", ["id"], unique=False)
        op.create_index(op.f("ix_ecommerce_carts_phone"), "ecommerce_carts", ["phone"], unique=False)
        op.create_index(op.f("ix_ecommerce_carts_status"), "ecommerce_carts", ["status"], unique=False)
        op.create_index(op.f("ix_ecommerce_carts_tenant_id"), "ecommerce_carts", ["tenant_id"], unique=False)

    if not inspector.has_table("ecommerce_return_requests"):
        op.create_table(
            "ecommerce_return_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=False),
            sa.Column("order_id", sa.String(), nullable=True),
            sa.Column("order_number", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("reason", sa.String(), nullable=True),
            sa.Column("item_ids", sa.Text(), nullable=True),
            sa.Column("eligibility", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_ecommerce_return_requests_id"), "ecommerce_return_requests", ["id"], unique=False)
        op.create_index(op.f("ix_ecommerce_return_requests_order_id"), "ecommerce_return_requests", ["order_id"], unique=False)
        op.create_index(op.f("ix_ecommerce_return_requests_order_number"), "ecommerce_return_requests", ["order_number"], unique=False)
        op.create_index(op.f("ix_ecommerce_return_requests_phone"), "ecommerce_return_requests", ["phone"], unique=False)
        op.create_index(op.f("ix_ecommerce_return_requests_status"), "ecommerce_return_requests", ["status"], unique=False)
        op.create_index(op.f("ix_ecommerce_return_requests_tenant_id"), "ecommerce_return_requests", ["tenant_id"], unique=False)

    if inspector.has_table("leads"):
        lead_columns = {column["name"] for column in inspector.get_columns("leads")}
        lead_indexes = {index["name"] for index in inspector.get_indexes("leads")}
        if "tenant_id" not in lead_columns:
            op.add_column("leads", sa.Column("tenant_id", sa.String(), nullable=True))
        bind.execute(sa.text("UPDATE leads SET tenant_id = 'default' WHERE tenant_id IS NULL OR tenant_id = ''"))
        if "ix_leads_tenant_id" not in lead_indexes:
            op.create_index(op.f("ix_leads_tenant_id"), "leads", ["tenant_id"], unique=False)

    if bind.dialect.name == "postgresql":
        policy_expression = (
            "current_setting('app.bypass_rls', true) = 'on' "
            "OR tenant_id = current_setting('app.tenant_id', true)"
        )
        for table_name in TENANT_TABLES:
            if not sa.inspect(bind).has_table(table_name):
                continue
            bind.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
            bind.execute(
                sa.text(
                    f"""
                    CREATE POLICY tenant_isolation_policy ON {table_name}
                    USING ({policy_expression})
                    WITH CHECK ({policy_expression})
                    """
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in reversed(TENANT_TABLES):
        if not inspector.has_table(table_name):
            continue
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
        if table_name == "leads":
            lead_indexes = {index["name"] for index in inspector.get_indexes("leads")}
            if "ix_leads_tenant_id" in lead_indexes:
                op.drop_index(op.f("ix_leads_tenant_id"), table_name="leads")
            if "tenant_id" in {column["name"] for column in inspector.get_columns("leads")}:
                op.drop_column("leads", "tenant_id")
            continue
        op.drop_table(table_name)
