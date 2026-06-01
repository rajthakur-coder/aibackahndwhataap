"""align AI users table with Alignlabs users shape

Revision ID: 20260601_0036
Revises: 20260601_0035
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260601_0036"
down_revision: Union[str, None] = "20260601_0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EXTRA_USER_COLUMNS = ("tenant_id", "role", "plan", "agent_enabled", "last_login_at")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("users")}

    if "tenant_id" in existing_columns and "ix_users_tenant_id" in existing_indexes:
        op.drop_index(op.f("ix_users_tenant_id"), table_name="users")

    with op.batch_alter_table("users") as batch_op:
        for column_name in EXTRA_USER_COLUMNS:
            if column_name in existing_columns:
                batch_op.drop_column(column_name)

    _set_column_server_default("users", "verified", sa.false())
    _set_column_server_default("users", "onboarding_completed", sa.false())


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    with op.batch_alter_table("users") as batch_op:
        if "tenant_id" not in existing_columns:
            batch_op.add_column(sa.Column("tenant_id", sa.String(length=80), nullable=True))
        if "role" not in existing_columns:
            batch_op.add_column(sa.Column("role", sa.String(), nullable=False, server_default="owner"))
        if "plan" not in existing_columns:
            batch_op.add_column(sa.Column("plan", sa.String(), nullable=False, server_default="free"))
        if "agent_enabled" not in existing_columns:
            batch_op.add_column(sa.Column("agent_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "last_login_at" not in existing_columns:
            batch_op.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE users
                SET tenant_id = left(
                    regexp_replace(
                        lower(coalesce(nullif(split_part(email, '@', 1), ''), 'user')),
                        '[^a-zA-Z0-9_.:-]+',
                        '',
                        'g'
                    ) || '-' || left(id::text, 8),
                    80
                )
                WHERE tenant_id IS NULL OR btrim(tenant_id) = '' OR tenant_id = 'default'
                """
            )
        )
    else:
        rows = bind.execute(sa.text("SELECT id, email FROM users WHERE tenant_id IS NULL OR trim(tenant_id) = ''")).mappings()
        for row in rows:
            local_part = str(row["email"] or "user").split("@", 1)[0].lower()
            clean_name = "".join(ch for ch in local_part if ch.isalnum() or ch in "_.:-") or "user"
            bind.execute(
                sa.text("UPDATE users SET tenant_id = :tenant_id WHERE id = :id"),
                {"tenant_id": f"{clean_name}-{str(row['id'])[:8]}"[:80], "id": row["id"]},
            )

    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=True)
    _set_column_server_default("users", "verified", sa.true())


def _set_column_server_default(table_name: str, column_name: str, default) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns(table_name)}
    if column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(column_name, server_default=default)
