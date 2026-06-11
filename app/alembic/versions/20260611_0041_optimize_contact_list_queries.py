"""optimize contact list queries

Revision ID: 20260611_0041
Revises: 20260611_0040
Create Date: 2026-06-11 00:41:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260611_0041"
down_revision = "20260611_0040"
branch_labels = None
depends_on = None


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_present(table_name: str, index_name: str) -> None:
    if _has_table(table_name) and index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _create_index_if_missing(
        "contacts",
        "ix_contacts_tenant_last_message_created",
        ["tenant_id", "last_message_time", "created_at"],
    )
    _create_index_if_missing(
        "contacts",
        "ix_contacts_tenant_status",
        ["tenant_id", "status"],
    )
    _create_index_if_missing(
        "contact_tags",
        "ix_contact_tags_tenant_contact",
        ["tenant_id", "contact_id"],
    )
    _create_index_if_missing(
        "contact_tags",
        "ix_contact_tags_tenant_tag",
        ["tenant_id", "tag_id"],
    )


def downgrade() -> None:
    _drop_index_if_present("contact_tags", "ix_contact_tags_tenant_tag")
    _drop_index_if_present("contact_tags", "ix_contact_tags_tenant_contact")
    _drop_index_if_present("contacts", "ix_contacts_tenant_status")
    _drop_index_if_present("contacts", "ix_contacts_tenant_last_message_created")
