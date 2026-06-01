"""enable tenant rls policies

Revision ID: 20260526_0020
Revises: 20260526_0019
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0020"
down_revision: Union[str, None] = "20260526_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = [
    "messages",
    "contacts",
    "integrations",
    "whatsapp_credentials",
    "whatsapp_templates",
    "webhook_events",
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


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    policy_expression = (
        "current_setting('app.bypass_rls', true) = 'on' "
        "OR tenant_id = current_setting('app.tenant_id', true)"
    )

    for table_name in TENANT_TABLES:
        if not inspector.has_table(table_name) or not _has_column(inspector, table_name, "tenant_id"):
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
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    for table_name in TENANT_TABLES:
        if not inspector.has_table(table_name):
            continue
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
        bind.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY"))
