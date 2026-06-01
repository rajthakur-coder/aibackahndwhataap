"""add configurable shopify catalog default categories

Revision ID: 20260525_0013
Revises: 20260525_0012
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0013"
down_revision: Union[str, None] = "20260525_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shopify_catalog_default_categories" in inspector.get_table_names():
        return

    op.create_table(
        "shopify_catalog_default_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("category_key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("visible", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shopify_catalog_default_categories_id"), "shopify_catalog_default_categories", ["id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_default_categories_tenant_id"), "shopify_catalog_default_categories", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_default_categories_connection_id"), "shopify_catalog_default_categories", ["connection_id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_default_categories_category_key"), "shopify_catalog_default_categories", ["category_key"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_default_categories_visible"), "shopify_catalog_default_categories", ["visible"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shopify_catalog_default_categories" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_shopify_catalog_default_categories_visible"), table_name="shopify_catalog_default_categories")
    op.drop_index(op.f("ix_shopify_catalog_default_categories_category_key"), table_name="shopify_catalog_default_categories")
    op.drop_index(op.f("ix_shopify_catalog_default_categories_connection_id"), table_name="shopify_catalog_default_categories")
    op.drop_index(op.f("ix_shopify_catalog_default_categories_tenant_id"), table_name="shopify_catalog_default_categories")
    op.drop_index(op.f("ix_shopify_catalog_default_categories_id"), table_name="shopify_catalog_default_categories")
    op.drop_table("shopify_catalog_default_categories")
