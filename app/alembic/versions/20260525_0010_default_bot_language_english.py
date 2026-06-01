"""default bot language to english

Revision ID: 20260525_0010
Revises: 20260525_0009
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0010"
down_revision: Union[str, None] = "20260525_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "bot_settings" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("bot_settings")}
    if "default_language" not in columns:
        return

    op.execute(
        "UPDATE bot_settings "
        "SET default_language = 'english' "
        "WHERE default_language IS NULL "
        "OR trim(default_language) = '' "
        "OR lower(default_language) IN ('auto', 'hinglish', 'hindi')"
    )


def downgrade() -> None:
    pass
