"""add AlignLabs-compatible auth users table

Revision ID: 20260526_0015
Revises: 20260525_0014
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0015"
down_revision: Union[str, None] = "20260525_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("users"):
        existing = {column["name"] for column in inspector.get_columns("users")}
        additions = {
            "tenant_id": sa.Column("tenant_id", sa.String(length=80), nullable=True),
            "role": sa.Column("role", sa.String(), nullable=False, server_default="owner"),
            "plan": sa.Column("plan", sa.String(), nullable=False, server_default="free"),
            "agent_enabled": sa.Column("agent_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            "last_login_at": sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        }
        with op.batch_alter_table("users") as batch_op:
            for name, column in additions.items():
                if name not in existing:
                    batch_op.add_column(column)
        return

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password", sa.String(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("credits", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role", sa.String(), nullable=False, server_default="owner"),
        sa.Column("plan", sa.String(), nullable=False, server_default="free"),
        sa.Column("agent_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    op.drop_index(op.f("ix_users_tenant_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
