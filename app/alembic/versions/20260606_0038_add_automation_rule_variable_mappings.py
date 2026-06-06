"""add explicit automation rule variable mappings

Revision ID: 20260606_0038
Revises: 20260602_0039
Create Date: 2026-06-06 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260606_0038"
down_revision = "20260602_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("automation_rules"):
        return
    columns = {column["name"] for column in inspector.get_columns("automation_rules")}
    if "variable_mappings" not in columns:
        op.add_column("automation_rules", sa.Column("variable_mappings", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("automation_rules"):
        return
    columns = {column["name"] for column in inspector.get_columns("automation_rules")}
    if "variable_mappings" in columns:
        op.drop_column("automation_rules", "variable_mappings")
