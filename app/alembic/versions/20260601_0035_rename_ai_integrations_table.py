"""rename AI integrations table away from Alignlabs integrations

Revision ID: 20260601_0035
Revises: 20260601_0034
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260601_0035"
down_revision: Union[str, None] = "20260601_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_RENAMES = {
    "ix_integrations_id": "ix_ai_integrations_id",
    "ix_integrations_provider": "ix_ai_integrations_provider",
    "ix_integrations_provider_account_id": "ix_ai_integrations_provider_account_id",
    "ix_integrations_status": "ix_ai_integrations_status",
    "ix_integrations_tenant_id": "ix_ai_integrations_tenant_id",
    "ix_integrations_user_id": "ix_ai_integrations_user_id",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("ai_integrations"):
        _rename_indexes(bind, "ai_integrations", INDEX_RENAMES)
        return
    if not inspector.has_table("integrations"):
        return

    op.rename_table("integrations", "ai_integrations")
    _rename_indexes(bind, "ai_integrations", INDEX_RENAMES)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("integrations"):
        _rename_indexes(bind, "integrations", {v: k for k, v in INDEX_RENAMES.items()})
        return
    if not inspector.has_table("ai_integrations"):
        return

    op.rename_table("ai_integrations", "integrations")
    _rename_indexes(bind, "integrations", {v: k for k, v in INDEX_RENAMES.items()})


def _rename_indexes(bind, table_name: str, renames: dict[str, str]) -> None:
    inspector = sa.inspect(bind)
    indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
    for old_name, new_name in renames.items():
        if old_name not in indexes or new_name in indexes:
            continue
        if bind.dialect.name == "postgresql":
            op.execute(sa.text(f'ALTER INDEX IF EXISTS "{old_name}" RENAME TO "{new_name}"'))
            continue
        index = indexes[old_name]
        op.drop_index(old_name, table_name=table_name)
        op.create_index(new_name, table_name, index["column_names"], unique=index.get("unique", False))
