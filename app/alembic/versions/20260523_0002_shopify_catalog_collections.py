"""add curated shopify catalog collections

Revision ID: 20260523_0002
Revises: 20260514_0001
Create Date: 2026-05-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260523_0002"
down_revision: Union[str, None] = "20260514_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shopify_catalog_collections" in inspector.get_table_names():
        return

    op.create_table(
        "shopify_catalog_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("shopify_collection_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("handle", sa.String(), nullable=True),
        sa.Column("product_count", sa.Integer(), nullable=True),
        sa.Column("visible", sa.String(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shopify_catalog_collections_id"), "shopify_catalog_collections", ["id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_collections_tenant_id"), "shopify_catalog_collections", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_collections_connection_id"), "shopify_catalog_collections", ["connection_id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_collections_shopify_collection_id"), "shopify_catalog_collections", ["shopify_collection_id"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_collections_handle"), "shopify_catalog_collections", ["handle"], unique=False)
    op.create_index(op.f("ix_shopify_catalog_collections_visible"), "shopify_catalog_collections", ["visible"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "shopify_catalog_collections" not in inspector.get_table_names():
        return

    op.drop_index(op.f("ix_shopify_catalog_collections_visible"), table_name="shopify_catalog_collections")
    op.drop_index(op.f("ix_shopify_catalog_collections_handle"), table_name="shopify_catalog_collections")
    op.drop_index(op.f("ix_shopify_catalog_collections_shopify_collection_id"), table_name="shopify_catalog_collections")
    op.drop_index(op.f("ix_shopify_catalog_collections_connection_id"), table_name="shopify_catalog_collections")
    op.drop_index(op.f("ix_shopify_catalog_collections_tenant_id"), table_name="shopify_catalog_collections")
    op.drop_index(op.f("ix_shopify_catalog_collections_id"), table_name="shopify_catalog_collections")
    op.drop_table("shopify_catalog_collections")
