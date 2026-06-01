"""tenant isolation audit hardening

Revision ID: 20260528_0033
Revises: 20260528_0032
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0033"
down_revision: Union[str, None] = "20260528_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("agency_tenant_access") and not _has_column(inspector, "agency_tenant_access", "tenant_id"):
        op.add_column(
            "agency_tenant_access",
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
        )
        op.create_index(op.f("ix_agency_tenant_access_tenant_id"), "agency_tenant_access", ["tenant_id"], unique=False)
        bind.execute(sa.text("UPDATE agency_tenant_access SET tenant_id = agency_tenant_id WHERE agency_tenant_id IS NOT NULL"))

    if bind.dialect.name != "postgresql":
        return

    policy_expression = (
        "current_setting('app.bypass_rls', true) = 'on' "
        "OR tenant_id = current_setting('app.tenant_id', true)"
    )
    for table_name in inspector.get_table_names():
        if table_name == "alembic_version" or not _has_column(sa.inspect(bind), table_name, "tenant_id"):
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
    if inspector.has_table("agency_tenant_access") and _has_column(inspector, "agency_tenant_access", "tenant_id"):
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON agency_tenant_access"))
            bind.execute(sa.text("ALTER TABLE agency_tenant_access NO FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text("ALTER TABLE agency_tenant_access DISABLE ROW LEVEL SECURITY"))
        op.drop_index(op.f("ix_agency_tenant_access_tenant_id"), table_name="agency_tenant_access")
        op.drop_column("agency_tenant_access", "tenant_id")
