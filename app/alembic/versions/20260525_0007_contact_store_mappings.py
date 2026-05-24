"""add contact store mappings

Revision ID: 20260525_0007
Revises: 20260524_0006
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525_0007"
down_revision = "20260524_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "contact_store_mappings" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "contact_store_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("normalized_phone", sa.String(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "normalized_phone", name="uq_contact_store_mapping_tenant_phone"),
    )
    op.create_index(op.f("ix_contact_store_mappings_id"), "contact_store_mappings", ["id"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_tenant_id"), "contact_store_mappings", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_phone"), "contact_store_mappings", ["phone"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_normalized_phone"), "contact_store_mappings", ["normalized_phone"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_connection_id"), "contact_store_mappings", ["connection_id"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_source"), "contact_store_mappings", ["source"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_status"), "contact_store_mappings", ["status"], unique=False)
    op.create_index(op.f("ix_contact_store_mappings_last_seen_at"), "contact_store_mappings", ["last_seen_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_store_mappings_last_seen_at"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_status"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_source"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_connection_id"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_normalized_phone"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_phone"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_tenant_id"), table_name="contact_store_mappings")
    op.drop_index(op.f("ix_contact_store_mappings_id"), table_name="contact_store_mappings")
    op.drop_table("contact_store_mappings")
