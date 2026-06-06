"""fix message template tenant uniqueness

Revision ID: 20260606_0037
Revises: 20260601_0036
Create Date: 2026-06-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260606_0037"
down_revision: Union[str, None] = "20260601_0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("message_templates"):
        return

    if bind.dialect.name == "postgresql":
        _drop_postgres_global_uniques(bind)
        _ensure_postgres_tenant_unique(bind)
        _ensure_postgres_name_index(bind)
        return

    existing_constraints = {item["name"] for item in inspector.get_unique_constraints("message_templates")}
    if "uq_message_templates_tenant_name" not in existing_constraints:
        op.create_unique_constraint(
            "uq_message_templates_tenant_name",
            "message_templates",
            ["tenant_id", "name"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("message_templates"):
        return

    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_message_templates_tenant_name'
                    ) THEN
                        ALTER TABLE message_templates
                        DROP CONSTRAINT uq_message_templates_tenant_name;
                    END IF;
                END $$;
                """
            )
        )
        return

    existing_constraints = {item["name"] for item in inspector.get_unique_constraints("message_templates")}
    if "uq_message_templates_tenant_name" in existing_constraints:
        op.drop_constraint("uq_message_templates_tenant_name", "message_templates", type_="unique")


def _drop_postgres_global_uniques(bind) -> None:
    for constraint_name in ("message_templates_name_key", "ix_message_templates_name"):
        bind.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                    ) THEN
                        ALTER TABLE message_templates DROP CONSTRAINT {constraint_name};
                    END IF;
                END $$;
                """
            )
        )

    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_class index_class
                    JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
                    WHERE index_class.relkind = 'i'
                      AND index_class.relname = 'ix_message_templates_name'
                      AND namespace.nspname = current_schema()
                ) THEN
                    DROP INDEX ix_message_templates_name;
                END IF;
            END $$;
            """
        )
    )


def _ensure_postgres_tenant_unique(bind) -> None:
    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'uq_message_templates_tenant_name'
                ) THEN
                    ALTER TABLE message_templates
                    ADD CONSTRAINT uq_message_templates_tenant_name UNIQUE (tenant_id, name);
                END IF;
            END $$;
            """
        )
    )


def _ensure_postgres_name_index(bind) -> None:
    bind.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_message_templates_name
            ON message_templates (name)
            """
        )
    )
