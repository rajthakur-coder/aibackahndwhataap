"""add knowledge base

Revision ID: 20260524_0006
Revises: 20260524_0005
Create Date: 2026-05-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260524_0006"
down_revision: Union[str, None] = "20260524_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "knowledge_bases" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("website_link", sa.Text(), nullable=True),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("about_company", sa.Text(), nullable=True),
        sa.Column("target_demographics", sa.Text(), nullable=True),
        sa.Column("logo", sa.Text(), nullable=True),
        sa.Column("socials", sa.Text(), nullable=True),
        sa.Column("page_images", sa.Text(), nullable=True),
        sa.Column("policies", sa.Text(), nullable=True),
        sa.Column("faqs", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_knowledge_bases_id"), "knowledge_bases", ["id"], unique=False)
    op.create_index(op.f("ix_knowledge_bases_tenant_id"), "knowledge_bases", ["tenant_id"], unique=True)


def downgrade() -> None:
    if "knowledge_bases" not in sa.inspect(op.get_bind()).get_table_names():
        return

    op.drop_index(op.f("ix_knowledge_bases_tenant_id"), table_name="knowledge_bases")
    op.drop_index(op.f("ix_knowledge_bases_id"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
