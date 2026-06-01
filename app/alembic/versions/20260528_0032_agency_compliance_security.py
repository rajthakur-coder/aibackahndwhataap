"""agency compliance security

Revision ID: 20260528_0032
Revises: 20260528_0031
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260528_0032"
down_revision: Union[str, None] = "20260528_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("agency_tenant_access"):
        op.create_table(
            "agency_tenant_access",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("agency_tenant_id", sa.String(length=80), nullable=False),
            sa.Column("client_tenant_id", sa.String(length=80), nullable=False),
            sa.Column("role", sa.String(length=40), nullable=True, server_default="reseller_admin"),
            sa.Column("status", sa.String(length=40), nullable=True, server_default="active"),
            sa.Column("white_label_name", sa.String(length=160), nullable=True),
            sa.Column("white_label_domain", sa.String(length=255), nullable=True),
            sa.Column("support_email", sa.String(length=255), nullable=True),
            sa.Column("settings_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("agency_tenant_id", "client_tenant_id", name="uq_agency_client_tenant"),
        )
        op.create_index(op.f("ix_agency_tenant_access_id"), "agency_tenant_access", ["id"], unique=False)
        op.create_index(op.f("ix_agency_tenant_access_agency_tenant_id"), "agency_tenant_access", ["agency_tenant_id"], unique=False)
        op.create_index(op.f("ix_agency_tenant_access_client_tenant_id"), "agency_tenant_access", ["client_tenant_id"], unique=False)
        op.create_index(op.f("ix_agency_tenant_access_role"), "agency_tenant_access", ["role"], unique=False)
        op.create_index(op.f("ix_agency_tenant_access_status"), "agency_tenant_access", ["status"], unique=False)

    if not inspector.has_table("data_principal_requests"):
        op.create_table(
            "data_principal_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=True, server_default="default"),
            sa.Column("phone", sa.String(), nullable=False),
            sa.Column("request_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=True, server_default="received"),
            sa.Column("purpose", sa.Text(), nullable=True),
            sa.Column("requester_email", sa.String(), nullable=True),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_data_principal_requests_id"), "data_principal_requests", ["id"], unique=False)
        op.create_index(op.f("ix_data_principal_requests_tenant_id"), "data_principal_requests", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_data_principal_requests_phone"), "data_principal_requests", ["phone"], unique=False)
        op.create_index(op.f("ix_data_principal_requests_request_type"), "data_principal_requests", ["request_type"], unique=False)
        op.create_index(op.f("ix_data_principal_requests_status"), "data_principal_requests", ["status"], unique=False)
        op.create_index(op.f("ix_data_principal_requests_due_at"), "data_principal_requests", ["due_at"], unique=False)

    if bind.dialect.name != "postgresql":
        return

    policy_expression = (
        "current_setting('app.bypass_rls', true) = 'on' "
        "OR tenant_id = current_setting('app.tenant_id', true)"
    )
    for table_name in ("data_principal_requests",):
        bind.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name}"))
        bind.execute(
            sa.text(
                f"""
                CREATE POLICY tenant_isolation_policy ON {table_name}
                USING ({policy_expression})
                WITH CHECK ({policy_expression})
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("data_principal_requests"):
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_policy ON data_principal_requests"))
            bind.execute(sa.text("ALTER TABLE data_principal_requests NO FORCE ROW LEVEL SECURITY"))
            bind.execute(sa.text("ALTER TABLE data_principal_requests DISABLE ROW LEVEL SECURITY"))
        op.drop_index(op.f("ix_data_principal_requests_due_at"), table_name="data_principal_requests")
        op.drop_index(op.f("ix_data_principal_requests_status"), table_name="data_principal_requests")
        op.drop_index(op.f("ix_data_principal_requests_request_type"), table_name="data_principal_requests")
        op.drop_index(op.f("ix_data_principal_requests_phone"), table_name="data_principal_requests")
        op.drop_index(op.f("ix_data_principal_requests_tenant_id"), table_name="data_principal_requests")
        op.drop_index(op.f("ix_data_principal_requests_id"), table_name="data_principal_requests")
        op.drop_table("data_principal_requests")

    if inspector.has_table("agency_tenant_access"):
        op.drop_index(op.f("ix_agency_tenant_access_status"), table_name="agency_tenant_access")
        op.drop_index(op.f("ix_agency_tenant_access_role"), table_name="agency_tenant_access")
        op.drop_index(op.f("ix_agency_tenant_access_client_tenant_id"), table_name="agency_tenant_access")
        op.drop_index(op.f("ix_agency_tenant_access_agency_tenant_id"), table_name="agency_tenant_access")
        op.drop_index(op.f("ix_agency_tenant_access_id"), table_name="agency_tenant_access")
        op.drop_table("agency_tenant_access")
