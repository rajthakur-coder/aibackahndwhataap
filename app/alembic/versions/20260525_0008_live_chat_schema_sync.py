"""sync live chat schema columns

Revision ID: 20260525_0008
Revises: 20260525_0007
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0008"
down_revision: Union[str, None] = "20260525_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if table_name in _tables() and column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    if table_name not in _tables():
        return
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_column_if_missing("messages", sa.Column("status", sa.String(), nullable=True))
    _add_column_if_missing("messages", sa.Column("message_type", sa.String(), nullable=True))
    _add_column_if_missing("messages", sa.Column("whatsapp_message_id", sa.String(), nullable=True))
    _create_index_if_missing("messages", op.f("ix_messages_status"), ["status"])
    _create_index_if_missing("messages", op.f("ix_messages_whatsapp_message_id"), ["whatsapp_message_id"], unique=True)

    _add_column_if_missing("contacts", sa.Column("profile_name", sa.String(), nullable=True))
    _add_column_if_missing("contacts", sa.Column("custom_name", sa.String(), nullable=True))
    _add_column_if_missing("contacts", sa.Column("remark", sa.Text(), nullable=True))
    _add_column_if_missing("contacts", sa.Column("status", sa.String(), nullable=True))
    _add_column_if_missing("contacts", sa.Column("created_at", sa.DateTime(), nullable=True))
    _add_column_if_missing("contacts", sa.Column("updated_at", sa.DateTime(), nullable=True))
    _create_index_if_missing("contacts", op.f("ix_contacts_status"), ["status"])


def downgrade() -> None:
    # Keep downgrade conservative because some installs may have these columns
    # from Base.metadata.create_all or startup repair code.
    pass
