"""add knowledge base contact fields

Revision ID: 20260612_0042
Revises: 20260611_0041
Create Date: 2026-06-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260612_0042"
down_revision: Union[str, None] = "20260611_0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _columns("knowledge_bases")
    if not existing:
        return
    if "contact_email" not in existing:
        op.add_column("knowledge_bases", sa.Column("contact_email", sa.String(), nullable=True))
    if "contact_phone" not in existing:
        op.add_column("knowledge_bases", sa.Column("contact_phone", sa.String(), nullable=True))


def downgrade() -> None:
    existing = _columns("knowledge_bases")
    if "contact_phone" in existing:
        op.drop_column("knowledge_bases", "contact_phone")
    if "contact_email" in existing:
        op.drop_column("knowledge_bases", "contact_email")
