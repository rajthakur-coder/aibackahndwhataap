"""add tenant isolation to handoff tickets

Revision ID: 20260528_0029
Revises: 20260528_0028
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0029"
down_revision: Union[str, None] = "20260528_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("handoff_tickets"):
        return

    if not _has_column(inspector, "handoff_tickets", "tenant_id"):
        op.add_column(
            "handoff_tickets",
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
        )
        op.create_index(op.f("ix_handoff_tickets_tenant_id"), "handoff_tickets", ["tenant_id"], unique=False)

    if bind.dialect.name != "postgresql":
        return

    bind.execute(sa.text("ALTER TABLE handoff_tickets ENABLE ROW LEVEL SECURITY"))
    bind.execute(sa.text("ALTER TABLE handoff_tickets FORCE ROW LEVEL SECURITY"))
    bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON handoff_tickets"))
    bind.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation_policy ON handoff_tickets
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR tenant_id = current_setting('app.tenant_id', true)
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR tenant_id = current_setting('app.tenant_id', true)
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("handoff_tickets"):
        return

    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON handoff_tickets"))
        bind.execute(sa.text("ALTER TABLE handoff_tickets NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text("ALTER TABLE handoff_tickets DISABLE ROW LEVEL SECURITY"))

    if _has_column(inspector, "handoff_tickets", "tenant_id"):
        op.drop_index(op.f("ix_handoff_tickets_tenant_id"), table_name="handoff_tickets")
        op.drop_column("handoff_tickets", "tenant_id")
