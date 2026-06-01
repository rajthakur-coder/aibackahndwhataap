"""skip onboarding for AI agent auth users

Revision ID: 20260526_0016
Revises: 20260526_0015
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0016"
down_revision: Union[str, None] = "20260526_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    op.execute("UPDATE users SET onboarding_completed = TRUE WHERE onboarding_completed IS DISTINCT FROM TRUE")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("onboarding_completed", server_default=sa.true(), existing_type=sa.Boolean())


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("onboarding_completed", server_default=sa.false(), existing_type=sa.Boolean())
