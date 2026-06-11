"""denormalize live chat contact summaries

Revision ID: 20260611_0040
Revises: 20260606_0038
Create Date: 2026-06-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260611_0040"
down_revision = "20260606_0038"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("contacts"):
        return

    columns = _columns("contacts")
    if "last_message" not in columns:
        op.add_column("contacts", sa.Column("last_message", sa.Text(), nullable=True))
    if "last_message_type" not in columns:
        op.add_column("contacts", sa.Column("last_message_type", sa.String(), nullable=True, server_default="text"))
    if "last_message_time" not in columns:
        op.add_column("contacts", sa.Column("last_message_time", sa.DateTime(timezone=True), nullable=True))
        op.create_index(op.f("ix_contacts_last_message_time"), "contacts", ["last_message_time"], unique=False)
    if "last_incoming_msg_time" not in columns:
        op.add_column("contacts", sa.Column("last_incoming_msg_time", sa.DateTime(timezone=True), nullable=True))
    if "unread_count" not in columns:
        op.add_column("contacts", sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"))

    if not inspector.has_table("messages") or bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text(
            """
            UPDATE contacts AS c
            SET
                last_message = latest.message,
                last_message_type = COALESCE(latest.message_type, 'text'),
                last_message_time = latest.created_at
            FROM (
                SELECT DISTINCT ON (tenant_id, phone)
                    tenant_id, phone, message, message_type, created_at, id
                FROM messages
                ORDER BY tenant_id, phone, created_at DESC NULLS LAST, id DESC
            ) AS latest
            WHERE c.tenant_id = latest.tenant_id AND c.phone = latest.phone
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE contacts AS c
            SET last_incoming_msg_time = latest.created_at
            FROM (
                SELECT DISTINCT ON (tenant_id, phone)
                    tenant_id, phone, created_at, id
                FROM messages
                WHERE direction = 'incoming'
                ORDER BY tenant_id, phone, created_at DESC NULLS LAST, id DESC
            ) AS latest
            WHERE c.tenant_id = latest.tenant_id AND c.phone = latest.phone
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE contacts AS c
            SET unread_count = unread.total
            FROM (
                SELECT tenant_id, phone, COUNT(*) AS total
                FROM messages
                WHERE direction = 'incoming' AND status != 'read'
                GROUP BY tenant_id, phone
            ) AS unread
            WHERE c.tenant_id = unread.tenant_id AND c.phone = unread.phone
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("contacts"):
        return

    columns = _columns("contacts")
    if "unread_count" in columns:
        op.drop_column("contacts", "unread_count")
    if "last_incoming_msg_time" in columns:
        op.drop_column("contacts", "last_incoming_msg_time")
    if "last_message_time" in columns:
        indexes = {index["name"] for index in inspector.get_indexes("contacts")}
        if op.f("ix_contacts_last_message_time") in indexes:
            op.drop_index(op.f("ix_contacts_last_message_time"), table_name="contacts")
        op.drop_column("contacts", "last_message_time")
    if "last_message_type" in columns:
        op.drop_column("contacts", "last_message_type")
    if "last_message" in columns:
        op.drop_column("contacts", "last_message")
