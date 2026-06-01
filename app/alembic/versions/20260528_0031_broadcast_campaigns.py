"""add broadcast campaigns

Revision ID: 20260528_0031
Revises: 20260528_0030
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260528_0031"
down_revision = "20260528_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "broadcast_campaigns" in inspector.get_table_names():
        return
    op.create_table(
        "broadcast_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("template", sa.String(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("variables", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("sent_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_broadcast_campaigns_id"), "broadcast_campaigns", ["id"], unique=False)
    op.create_index(op.f("ix_broadcast_campaigns_tenant_id"), "broadcast_campaigns", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_broadcast_campaigns_name"), "broadcast_campaigns", ["name"], unique=False)
    op.create_index(op.f("ix_broadcast_campaigns_template"), "broadcast_campaigns", ["template"], unique=False)
    op.create_index(op.f("ix_broadcast_campaigns_status"), "broadcast_campaigns", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "broadcast_campaigns" not in inspector.get_table_names():
        return
    op.drop_index(op.f("ix_broadcast_campaigns_status"), table_name="broadcast_campaigns")
    op.drop_index(op.f("ix_broadcast_campaigns_template"), table_name="broadcast_campaigns")
    op.drop_index(op.f("ix_broadcast_campaigns_name"), table_name="broadcast_campaigns")
    op.drop_index(op.f("ix_broadcast_campaigns_tenant_id"), table_name="broadcast_campaigns")
    op.drop_index(op.f("ix_broadcast_campaigns_id"), table_name="broadcast_campaigns")
    op.drop_table("broadcast_campaigns")
