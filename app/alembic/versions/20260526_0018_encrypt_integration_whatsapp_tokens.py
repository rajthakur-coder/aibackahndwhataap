"""encrypt integration and whatsapp tokens

Revision ID: 20260526_0018
Revises: 20260526_0017
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.modules.ecommerce.shared.token_service import encrypt_token


revision: str = "20260526_0018"
down_revision: Union[str, None] = "20260526_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("integrations"):
        rows = bind.execute(
            sa.text("select id, access_token, refresh_token from integrations")
        ).mappings().all()
        for row in rows:
            bind.execute(
                sa.text(
                    "update integrations set access_token=:access_token, refresh_token=:refresh_token where id=:id"
                ),
                {
                    "id": row["id"],
                    "access_token": encrypt_token(row["access_token"]),
                    "refresh_token": encrypt_token(row["refresh_token"]),
                },
            )

    if inspector.has_table("whatsapp_credentials"):
        rows = bind.execute(
            sa.text("select id, token, authorization_token from whatsapp_credentials")
        ).mappings().all()
        for row in rows:
            bind.execute(
                sa.text(
                    "update whatsapp_credentials set token=:token, authorization_token=:authorization_token where id=:id"
                ),
                {
                    "id": row["id"],
                    "token": encrypt_token(row["token"]),
                    "authorization_token": encrypt_token(row["authorization_token"]),
                },
            )


def downgrade() -> None:
    pass
