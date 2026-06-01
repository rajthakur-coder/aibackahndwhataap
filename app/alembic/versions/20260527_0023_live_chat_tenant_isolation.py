"""add tenant isolation to live chat tables

Revision ID: 20260527_0023
Revises: 20260526_0022
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260527_0023"
down_revision: Union[str, None] = "20260526_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in _inspector().get_indexes(table_name)}


def _constraints(table_name: str) -> set[str]:
    return {constraint["name"] for constraint in _inspector().get_unique_constraints(table_name)}


def _add_tenant_column(table_name: str) -> None:
    if not _has_table(table_name):
        return
    if "tenant_id" not in _columns(table_name):
        op.add_column(
            table_name,
            sa.Column("tenant_id", sa.String(length=80), server_default="default", nullable=False),
        )
    index_name = op.f(f"ix_{table_name}_tenant_id")
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, ["tenant_id"], unique=False)


def upgrade() -> None:
    _add_tenant_column("messages")
    _add_tenant_column("contacts")

    if _has_table("contacts"):
        bind = op.get_bind()
        for constraint_name in ("contacts_phone_key", "uq_contacts_phone"):
            if constraint_name in _constraints("contacts"):
                op.drop_constraint(constraint_name, "contacts", type_="unique")
        if "ix_contacts_phone" in _indexes("contacts"):
            bind.execute(sa.text("DROP INDEX IF EXISTS ix_contacts_phone"))
        if "ix_contacts_phone" not in _indexes("contacts"):
            op.create_index(op.f("ix_contacts_phone"), "contacts", ["phone"], unique=False)
        if "uq_contacts_tenant_phone" not in _constraints("contacts"):
            op.create_unique_constraint(
                "uq_contacts_tenant_phone",
                "contacts",
                ["tenant_id", "phone"],
            )

    if op.get_bind().dialect.name == "postgresql":
        for table_name in ("messages", "contacts"):
            if not _has_table(table_name):
                continue
            op.get_bind().execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
            op.get_bind().execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
            op.get_bind().execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
            op.get_bind().execute(
                sa.text(
                    f"""
                    CREATE POLICY tenant_isolation_policy ON {table_name}
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
    pass
