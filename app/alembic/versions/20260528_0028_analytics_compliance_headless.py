"""analytics compliance headless foundation

Revision ID: 20260528_0028
Revises: 20260528_0027
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0028"
down_revision: Union[str, None] = "20260528_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = {
    "customer_consents": [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("consent_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ],
    "csat_responses": [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ],
    "tenant_custom_tools": [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("input_schema", sa.Text(), nullable=True),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("fallback", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    ],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name, columns in TABLES.items():
        if inspector.has_table(table_name):
            continue
        op.create_table(table_name, *columns)
        op.create_index(op.f(f"ix_{table_name}_id"), table_name, ["id"], unique=False)
        op.create_index(op.f(f"ix_{table_name}_tenant_id"), table_name, ["tenant_id"], unique=False)
    if bind.dialect.name == "postgresql":
        policy_expression = "current_setting('app.bypass_rls', true) = 'on' OR tenant_id = current_setting('app.tenant_id', true)"
        for table_name in TABLES:
            bind.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
            bind.execute(sa.text(f"CREATE POLICY tenant_isolation_policy ON {table_name} USING ({policy_expression}) WITH CHECK ({policy_expression})"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in reversed(tuple(TABLES.keys())):
        if not inspector.has_table(table_name):
            continue
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
        op.drop_table(table_name)
