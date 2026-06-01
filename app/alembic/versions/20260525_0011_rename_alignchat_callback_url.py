"""rename nerochat callback url to alignchat

Revision ID: 20260525_0011
Revises: 20260525_0010
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0011"
down_revision: Union[str, None] = "20260525_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "whatsapp_credentials" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("whatsapp_credentials")}
    if "nerochat_callback_url" in columns and "alignchat_callback_url" not in columns:
        op.alter_column(
            "whatsapp_credentials",
            "nerochat_callback_url",
            new_column_name="alignchat_callback_url",
            existing_type=sa.Text(),
            existing_nullable=True,
        )
    elif "alignchat_callback_url" not in columns:
        op.add_column("whatsapp_credentials", sa.Column("alignchat_callback_url", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "whatsapp_credentials" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("whatsapp_credentials")}
    if "alignchat_callback_url" in columns and "nerochat_callback_url" not in columns:
        op.alter_column(
            "whatsapp_credentials",
            "alignchat_callback_url",
            new_column_name="nerochat_callback_url",
            existing_type=sa.Text(),
            existing_nullable=True,
        )
    elif "nerochat_callback_url" not in columns:
        op.add_column("whatsapp_credentials", sa.Column("nerochat_callback_url", sa.Text(), nullable=True))
