"""enforce rls for app database role

Revision ID: 20260526_0021
Revises: 20260526_0020
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0021"
down_revision: Union[str, None] = "20260526_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                EXECUTE format('ALTER ROLE %I NOBYPASSRLS', current_user);
            EXCEPTION
                WHEN insufficient_privilege THEN
                    RAISE NOTICE 'Current role cannot be altered to NOBYPASSRLS. Use a non-BYPASSRLS app role for true RLS enforcement.';
            END $$;
            """
        )
    )


def downgrade() -> None:
    pass
