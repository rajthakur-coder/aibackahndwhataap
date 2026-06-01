"""add message payload

Revision ID: 20260525_0014
Revises: 20260525_0013
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0014"
down_revision: Union[str, None] = "20260525_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "payload" in columns:
        return

    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("payload", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("messages")}
    if "payload" not in columns:
        return

    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("payload")
