"""tenant scope support tables

Revision ID: 20260528_0030
Revises: 20260528_0029
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0030"
down_revision: Union[str, None] = "20260528_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = [
    "agent_actions",
    "appointments",
    "order_statuses",
    "customer_profiles",
    "customer_memories",
    "message_templates",
    "automation_rules",
    "automation_events",
    "automation_executions",
    "tags",
    "contact_tags",
    "whatsapp_interaction_events",
]


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in TENANT_TABLES:
        if not inspector.has_table(table_name) or _has_column(inspector, table_name, "tenant_id"):
            continue
        op.add_column(
            table_name,
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
        )
        op.create_index(op.f(f"ix_{table_name}_tenant_id"), table_name, ["tenant_id"], unique=False)

    if bind.dialect.name != "postgresql":
        return

    for table_name, constraint_name in (
        ("tags", "uq_tags_name"),
        ("message_templates", "message_templates_name_key"),
        ("customer_profiles", "customer_profiles_phone_key"),
    ):
        bind.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                    ) THEN
                        ALTER TABLE {table_name} DROP CONSTRAINT {constraint_name};
                    END IF;
                END $$;
                """
            )
        )

    for constraint_name, constraint_sql in (
        ("uq_tags_tenant_name", "ALTER TABLE tags ADD CONSTRAINT uq_tags_tenant_name UNIQUE (tenant_id, name)"),
        ("uq_message_templates_tenant_name", "ALTER TABLE message_templates ADD CONSTRAINT uq_message_templates_tenant_name UNIQUE (tenant_id, name)"),
        ("uq_customer_profiles_tenant_phone", "ALTER TABLE customer_profiles ADD CONSTRAINT uq_customer_profiles_tenant_phone UNIQUE (tenant_id, phone)"),
    ):
        bind.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                    ) THEN
                        {constraint_sql};
                    END IF;
                END $$;
                """
            )
        )

    policy_expression = (
        "current_setting('app.bypass_rls', true) = 'on' "
        "OR tenant_id = current_setting('app.tenant_id', true)"
    )
    for table_name in TENANT_TABLES:
        if not inspector.has_table(table_name) or not _has_column(sa.inspect(bind), table_name, "tenant_id"):
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
    for table_name in TENANT_TABLES:
        if not inspector.has_table(table_name):
            continue
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
            bind.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY"))
        if _has_column(inspector, table_name, "tenant_id"):
            op.drop_index(op.f(f"ix_{table_name}_tenant_id"), table_name=table_name)
            op.drop_column(table_name, "tenant_id")
