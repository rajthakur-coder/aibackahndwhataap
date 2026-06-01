"""create rls runtime role

Revision ID: 20260526_0022
Revises: 20260526_0021
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260526_0022"
down_revision: Union[str, None] = "20260526_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "ai_agent_runtime"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                    CREATE ROLE {RUNTIME_ROLE} NOLOGIN NOBYPASSRLS;
                END IF;
            END $$;
            """
        )
    )
    bind.execute(sa.text(f"ALTER ROLE {RUNTIME_ROLE} NOBYPASSRLS"))
    bind.execute(sa.text(f"GRANT {RUNTIME_ROLE} TO CURRENT_USER"))
    bind.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}"))
    bind.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {RUNTIME_ROLE}"))
    bind.execute(sa.text(f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}"))
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE}"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {RUNTIME_ROLE}"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text(f"REVOKE {RUNTIME_ROLE} FROM CURRENT_USER"))
    bind.execute(sa.text(f"DROP ROLE IF EXISTS {RUNTIME_ROLE}"))
