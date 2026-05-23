"""add bot menu settings and per-store toggle

Revision ID: 20260523_0004
Revises: 20260523_0003
Create Date: 2026-05-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260523_0004"
down_revision: Union[str, None] = "20260523_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "ecommerce_connections" in tables and "bot_enabled" not in _columns("ecommerce_connections"):
        op.add_column("ecommerce_connections", sa.Column("bot_enabled", sa.String(), nullable=True))
        op.create_index(op.f("ix_ecommerce_connections_bot_enabled"), "ecommerce_connections", ["bot_enabled"], unique=False)
    if "bot_settings" in tables:
        columns = _columns("bot_settings")
        for column in [
            sa.Column("welcome_message", sa.Text(), nullable=True),
            sa.Column("offline_message", sa.Text(), nullable=True),
            sa.Column("main_menu_buttons", sa.Text(), nullable=True),
        ]:
            if column.name not in columns:
                op.add_column("bot_settings", column)


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "bot_settings" in tables:
        columns = _columns("bot_settings")
        for name in ("main_menu_buttons", "offline_message", "welcome_message"):
            if name in columns:
                op.drop_column("bot_settings", name)
    if "ecommerce_connections" in tables and "bot_enabled" in _columns("ecommerce_connections"):
        op.drop_index(op.f("ix_ecommerce_connections_bot_enabled"), table_name="ecommerce_connections")
        op.drop_column("ecommerce_connections", "bot_enabled")
