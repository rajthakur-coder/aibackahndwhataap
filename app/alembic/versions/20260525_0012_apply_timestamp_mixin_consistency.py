"""apply timestamp mixin consistency

Revision ID: 20260525_0012
Revises: 20260525_0011
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525_0012"
down_revision = "20260525_0011"
branch_labels = None
depends_on = None


TIMESTAMP_COLUMNS = {
    "agent_actions": ("updated_at",),
    "bot_settings": ("created_at",),
    "contact_tags": ("updated_at",),
    "customer_memories": ("updated_at",),
    "knowledge_bases": ("created_at",),
    "messages": ("updated_at",),
    "webhook_events": ("updated_at",),
    "whatsapp_interaction_events": ("updated_at",),
}


def _existing_columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    for table_name, column_names in TIMESTAMP_COLUMNS.items():
        existing_columns = _existing_columns(table_name)
        with op.batch_alter_table(table_name) as batch_op:
            for column_name in column_names:
                if column_name not in existing_columns:
                    batch_op.add_column(sa.Column(column_name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for table_name, column_names in reversed(TIMESTAMP_COLUMNS.items()):
        existing_columns = _existing_columns(table_name)
        with op.batch_alter_table(table_name) as batch_op:
            for column_name in reversed(column_names):
                if column_name in existing_columns:
                    batch_op.drop_column(column_name)
