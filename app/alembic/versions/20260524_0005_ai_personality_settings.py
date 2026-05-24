"""add ai personality settings

Revision ID: 20260524_0005
Revises: 20260523_0004
Create Date: 2026-05-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260524_0005"
down_revision: Union[str, None] = "20260523_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "bot_settings" not in sa.inspect(op.get_bind()).get_table_names():
        return

    columns = _columns("bot_settings")
    for column in [
        sa.Column("ai_personality", sa.String(), nullable=True),
        sa.Column("ai_tone", sa.String(), nullable=True),
        sa.Column("response_length", sa.String(), nullable=True),
        sa.Column("custom_instructions", sa.Text(), nullable=True),
    ]:
        if column.name not in columns:
            op.add_column("bot_settings", column)


def downgrade() -> None:
    if "bot_settings" not in sa.inspect(op.get_bind()).get_table_names():
        return

    columns = _columns("bot_settings")
    for name in ("custom_instructions", "response_length", "ai_tone", "ai_personality"):
        if name in columns:
            op.drop_column("bot_settings", name)
