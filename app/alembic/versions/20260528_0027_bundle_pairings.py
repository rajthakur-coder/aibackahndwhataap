"""create bundle pairings

Revision ID: 20260528_0027
Revises: 20260528_0026
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0027"
down_revision: Union[str, None] = "20260528_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("ecommerce_bundle_pairings"):
        op.create_table(
            "ecommerce_bundle_pairings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=True),
            sa.Column("primary_sku", sa.String(), nullable=False),
            sa.Column("paired_skus", sa.Text(), nullable=True),
            sa.Column("discount_type", sa.String(), nullable=True),
            sa.Column("discount_value", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "primary_sku", name="uq_bundle_pairing_tenant_primary_sku"),
        )
        op.create_index(op.f("ix_ecommerce_bundle_pairings_id"), "ecommerce_bundle_pairings", ["id"], unique=False)
        op.create_index(op.f("ix_ecommerce_bundle_pairings_primary_sku"), "ecommerce_bundle_pairings", ["primary_sku"], unique=False)
        op.create_index(op.f("ix_ecommerce_bundle_pairings_status"), "ecommerce_bundle_pairings", ["status"], unique=False)
        op.create_index(op.f("ix_ecommerce_bundle_pairings_tenant_id"), "ecommerce_bundle_pairings", ["tenant_id"], unique=False)

    if bind.dialect.name == "postgresql":
        policy_expression = (
            "current_setting('app.bypass_rls', true) = 'on' "
            "OR tenant_id = current_setting('app.tenant_id', true)"
        )
        bind.execute(sa.text("ALTER TABLE ecommerce_bundle_pairings ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE ecommerce_bundle_pairings FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON ecommerce_bundle_pairings"))
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation_policy ON ecommerce_bundle_pairings
                USING ({policy_expression})
                WITH CHECK ({policy_expression})
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("ecommerce_bundle_pairings"):
        return
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON ecommerce_bundle_pairings"))
    op.drop_table("ecommerce_bundle_pairings")
