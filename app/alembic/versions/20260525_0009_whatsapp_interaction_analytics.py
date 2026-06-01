"""add whatsapp interaction analytics

Revision ID: 20260525_0009
Revises: 20260525_0008
Create Date: 2026-05-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260525_0009"
down_revision: Union[str, None] = "20260525_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _indexes(table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if "whatsapp_interaction_events" not in _tables():
        op.create_table(
            "whatsapp_interaction_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("message_id", sa.String(), nullable=True),
            sa.Column("interaction_id", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("target_url", sa.Text(), nullable=True),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_id"), ["id"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_phone"), ["phone"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_event_type"), ["event_type"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_source"), ["source"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_message_id"), ["message_id"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_interaction_id"), ["interaction_id"])
    _create_index_if_missing("whatsapp_interaction_events", op.f("ix_whatsapp_interaction_events_created_at"), ["created_at"])


def downgrade() -> None:
    if "whatsapp_interaction_events" in _tables():
        op.drop_table("whatsapp_interaction_events")
