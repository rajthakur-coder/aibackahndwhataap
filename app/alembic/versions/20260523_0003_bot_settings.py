"""add bot settings

Revision ID: 20260523_0003
Revises: 20260523_0002
Create Date: 2026-05-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260523_0003"
down_revision: Union[str, None] = "20260523_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "bot_settings" in inspector.get_table_names():
        return

    op.create_table(
        "bot_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("bot_enabled", sa.String(), nullable=True),
        sa.Column("default_language", sa.String(), nullable=True),
        sa.Column("fallback_message", sa.Text(), nullable=True),
        sa.Column("handoff_keywords", sa.Text(), nullable=True),
        sa.Column("business_hours_enabled", sa.String(), nullable=True),
        sa.Column("business_hours_start", sa.String(), nullable=True),
        sa.Column("business_hours_end", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bot_settings_id"), "bot_settings", ["id"], unique=False)
    op.create_index(op.f("ix_bot_settings_tenant_id"), "bot_settings", ["tenant_id"], unique=True)
    op.create_index(op.f("ix_bot_settings_bot_enabled"), "bot_settings", ["bot_enabled"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "bot_settings" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_bot_settings_bot_enabled"), table_name="bot_settings")
    op.drop_index(op.f("ix_bot_settings_tenant_id"), table_name="bot_settings")
    op.drop_index(op.f("ix_bot_settings_id"), table_name="bot_settings")
    op.drop_table("bot_settings")
