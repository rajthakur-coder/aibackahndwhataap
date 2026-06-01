"""make users tenant id the auth source of truth

Revision ID: 20260527_0024
Revises: 20260527_0023
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260527_0024"
down_revision: Union[str, None] = "20260527_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    if not _has_table("users"):
        return

    if "tenant_id" not in _columns("users"):
        op.add_column("users", sa.Column("tenant_id", sa.String(length=80), nullable=True))

    bind = op.get_bind()
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
        rows = bind.execute(
            sa.text("SELECT id, email FROM users WHERE tenant_id IS NULL OR trim(tenant_id) = '' OR tenant_id = 'default'")
        ).mappings().all()
        for row in rows:
            local_part = str(row["email"] or "user").split("@", 1)[0].lower()
            tenant_id = "".join(ch for ch in local_part if ch.isalnum() or ch in "_.:-") or "user"
            tenant_id = f"{tenant_id}-{str(row['id'])[:8]}"[:80]
            bind.execute(
                sa.text("UPDATE users SET tenant_id = :tenant_id WHERE id = :id"),
                {"tenant_id": tenant_id, "id": row["id"]},
            )

    if bind.dialect.name == "postgresql":
        op.alter_column("users", "tenant_id", existing_type=sa.String(length=80), nullable=False)

    if "ix_users_tenant_id" not in _indexes("users"):
        op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=True)


def downgrade() -> None:
    pass
