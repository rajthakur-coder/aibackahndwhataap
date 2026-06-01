"""create tenant configs

Revision ID: 20260528_0025
Revises: 20260527_0024
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0025"
down_revision: Union[str, None] = "20260527_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("tenant_configs"):
        op.create_table(
            "tenant_configs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(length=80), nullable=False),
            sa.Column("brand_name", sa.String(length=160), nullable=False),
            sa.Column("brand_voice_prompt", sa.Text(), nullable=True),
            sa.Column("return_policy", sa.Text(), nullable=True),
            sa.Column("shipping_policy", sa.Text(), nullable=True),
            sa.Column("warranty_policy", sa.Text(), nullable=True),
            sa.Column("discount_rules", sa.Text(), nullable=True),
            sa.Column("categories", sa.Text(), nullable=True),
            sa.Column("support_email", sa.String(length=255), nullable=True),
            sa.Column("support_sla_hours", sa.Integer(), nullable=True),
            sa.Column("default_emoji", sa.String(length=16), nullable=True),
            sa.Column("default_tone", sa.String(length=80), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", name="uq_tenant_configs_tenant_id"),
        )
        op.create_index(op.f("ix_tenant_configs_id"), "tenant_configs", ["id"], unique=False)
        op.create_index(op.f("ix_tenant_configs_tenant_id"), "tenant_configs", ["tenant_id"], unique=False)

    if bind.dialect.name == "postgresql":
        policy_expression = (
            "current_setting('app.bypass_rls', true) = 'on' "
            "OR tenant_id = current_setting('app.tenant_id', true)"
        )
        bind.execute(sa.text("ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE tenant_configs FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON tenant_configs"))
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation_policy ON tenant_configs
                USING ({policy_expression})
                WITH CHECK ({policy_expression})
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("tenant_configs"):
        return
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON tenant_configs"))
    op.drop_index(op.f("ix_tenant_configs_tenant_id"), table_name="tenant_configs")
    op.drop_index(op.f("ix_tenant_configs_id"), table_name="tenant_configs")
    op.drop_table("tenant_configs")
