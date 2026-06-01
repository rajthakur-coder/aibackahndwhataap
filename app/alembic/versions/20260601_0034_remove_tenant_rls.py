"""remove tenant row level security

Revision ID: 20260601_0034
Revises: 20260528_0033
Create Date: 2026-06-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260601_0034"
down_revision: Union[str, None] = "20260528_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "ai_agent_runtime"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text(
            """
            DO $$
            DECLARE
                table_record record;
            BEGIN
                FOR table_record IN
                    SELECT n.nspname AS schema_name, c.relname AS table_name
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind = 'r'
                      AND n.nspname = current_schema()
                LOOP
                    EXECUTE format(
                        'DROP POLICY IF EXISTS tenant_isolation_policy ON %I.%I',
                        table_record.schema_name,
                        table_record.table_name
                    );
                    EXECUTE format(
                        'ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                        table_record.schema_name,
                        table_record.table_name
                    );
                    EXECUTE format(
                        'ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                        table_record.schema_name,
                        table_record.table_name
                    );
                END LOOP;
            END $$;
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                    EXECUTE 'REVOKE {RUNTIME_ROLE} FROM CURRENT_USER';
                    EXECUTE 'DROP OWNED BY {RUNTIME_ROLE}';
                    EXECUTE 'DROP ROLE {RUNTIME_ROLE}';
                END IF;
            EXCEPTION
                WHEN insufficient_privilege OR dependent_objects_still_exist THEN
                    RAISE NOTICE 'Could not drop {RUNTIME_ROLE}; continuing without RLS runtime role usage.';
            END $$;
            """
        )
    )


def downgrade() -> None:
    return None
