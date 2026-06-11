# from sqlalchemy import inspect, text

# from app.db.session import Base


# def ensure_live_chat_message_columns(connection) -> None:
#     inspector = inspect(connection)
#     if not inspector.has_table("messages"):
#         return

#     existing = {column["name"] for column in inspector.get_columns("messages")}
#     columns = {
#         "tenant_id": "VARCHAR(80) DEFAULT 'default'",
#         "status": "VARCHAR",
#         "message_type": "VARCHAR",
#         "payload": "TEXT",
#         "whatsapp_message_id": "VARCHAR",
#     }
#     for name, ddl_type in columns.items():
#         if name not in existing:
#             connection.execute(text(f"ALTER TABLE messages ADD COLUMN {name} {ddl_type}"))


# def ensure_contact_columns(connection) -> None:
#     inspector = inspect(connection)
#     if not inspector.has_table("contacts"):
#         return

#     existing = {column["name"] for column in inspector.get_columns("contacts")}
#     columns = {
#         "tenant_id": "VARCHAR(80) DEFAULT 'default'",
#         "profile_name": "VARCHAR",
#         "custom_name": "VARCHAR",
#         "remark": "TEXT",
#         "status": "VARCHAR DEFAULT 'Active'",
#         "created_at": "TIMESTAMP",
#         "updated_at": "TIMESTAMP",
#     }
#     for name, ddl_type in columns.items():
#         if name not in existing:
#             connection.execute(text(f"ALTER TABLE contacts ADD COLUMN {name} {ddl_type}"))


# def ensure_bot_settings_columns(connection) -> None:
#     inspector = inspect(connection)
#     if not inspector.has_table("bot_settings"):
#         return

#     existing = {column["name"] for column in inspector.get_columns("bot_settings")}
#     columns = {
#         "ai_personality": "VARCHAR",
#         "ai_tone": "VARCHAR",
#         "response_length": "VARCHAR",
#         "custom_instructions": "TEXT",
#         "brand_prompt": "TEXT",
#     }
#     for name, ddl_type in columns.items():
#         if name not in existing:
#             connection.execute(text(f"ALTER TABLE bot_settings ADD COLUMN {name} {ddl_type}"))


# def ensure_contact_store_mapping_columns(connection) -> None:
#     inspector = inspect(connection)
#     if not inspector.has_table("contact_store_mappings"):
#         return

#     existing = {column["name"] for column in inspector.get_columns("contact_store_mappings")}
#     columns = {
#         "last_seen_at": "TIMESTAMP",
#     }
#     for name, ddl_type in columns.items():
#         if name not in existing:
#             connection.execute(text(f"ALTER TABLE contact_store_mappings ADD COLUMN {name} {ddl_type}"))


# def ensure_user_columns(connection) -> None:
#     inspector = inspect(connection)
#     if not inspector.has_table("users"):
#         return

#     existing = {column["name"] for column in inspector.get_columns("users")}
#     columns = {
#         "tenant_id": "VARCHAR(80)",
#         "role": "VARCHAR DEFAULT 'owner'",
#         "plan": "VARCHAR DEFAULT 'free'",
#         "agent_enabled": "BOOLEAN DEFAULT TRUE",
#         "last_login_at": "TIMESTAMP",
#     }
#     for name, ddl_type in columns.items():
#         if name not in existing:
#             connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl_type}"))

#     existing = {column["name"] for column in inspector.get_columns("users")}
#     if "tenant_id" in existing:
#         if connection.dialect.name == "postgresql":
#             connection.execute(
#                 text(
#                     """
#                     UPDATE users
#                     SET tenant_id = left(
#                         regexp_replace(
#                             lower(coalesce(nullif(split_part(email, '@', 1), ''), 'user')),
#                             '[^a-zA-Z0-9_.:-]+',
#                             '',
#                             'g'
#                         ) || '-' || left(id::text, 8),
#                         80
#                     )
#                     WHERE tenant_id IS NULL OR btrim(tenant_id) = '' OR tenant_id = 'default'
#                     """
#                 )
#             )
#             connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_tenant_id ON users (tenant_id)"))
#         else:
#             rows = connection.execute(
#                 text("SELECT id, email FROM users WHERE tenant_id IS NULL OR trim(tenant_id) = '' OR tenant_id = 'default'")
#             ).mappings().all()
#             for row in rows:
#                 local_part = str(row["email"] or "user").split("@", 1)[0].lower()
#                 tenant_id = "".join(ch for ch in local_part if ch.isalnum() or ch in "_.:-") or "user"
#                 tenant_id = f"{tenant_id}-{str(row['id'])[:8]}"[:80]
#                 connection.execute(
#                     text("UPDATE users SET tenant_id = :tenant_id WHERE id = :id"),
#                     {"tenant_id": tenant_id, "id": row["id"]},
#                 )


# def initialize_database_schema(connection) -> None:
#     is_postgres = connection.dialect.name == "postgresql"
#     if is_postgres:
#         connection.execute(text("SELECT pg_advisory_lock(hashtext('ai_whatsapp_schema_init'))"))

#     try:
#         Base.metadata.create_all(bind=connection)
#         ensure_live_chat_message_columns(connection)
#         ensure_contact_columns(connection)
#         ensure_bot_settings_columns(connection)
#         ensure_contact_store_mapping_columns(connection)
#         ensure_user_columns(connection)
#     finally:
#         if is_postgres:
#             connection.execute(text("SELECT pg_advisory_unlock(hashtext('ai_whatsapp_schema_init'))"))

















from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.db.session import Base
import app.db.base  # noqa: F401 - populate Base.metadata before create_all


REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "messages": {
        "tenant_id": "VARCHAR(80) DEFAULT 'default'",
        "status": "VARCHAR",
        "message_type": "VARCHAR",
        "payload": "TEXT",
        "whatsapp_message_id": "VARCHAR",
    },
    "contacts": {
        "tenant_id": "VARCHAR(80) DEFAULT 'default'",
        "profile_name": "VARCHAR",
        "custom_name": "VARCHAR",
        "remark": "TEXT",
        "status": "VARCHAR DEFAULT 'Active'",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    },
    "bot_settings": {
        "ai_personality": "VARCHAR",
        "ai_tone": "VARCHAR",
        "response_length": "VARCHAR",
        "custom_instructions": "TEXT",
        "brand_prompt": "TEXT",
    },
    "contact_store_mappings": {
        "last_seen_at": "TIMESTAMP",
    },
    "knowledge_bases": {
        "contact_email": "VARCHAR",
        "contact_phone": "VARCHAR",
    },
}


def add_missing_columns(connection: Connection, inspector, table_name: str, columns: dict[str, str]) -> None:
    if not inspector.has_table(table_name):
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    for column_name, ddl_type in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_type}")
            )


def ensure_required_columns(connection: Connection) -> None:
    inspector = inspect(connection)

    for table_name, columns in REQUIRED_COLUMNS.items():
        add_missing_columns(connection, inspector, table_name, columns)


def initialize_database_schema(connection: Connection) -> None:
    is_postgres = connection.dialect.name == "postgresql"

    if is_postgres:
        connection.execute(text("SELECT pg_advisory_lock(hashtext('ai_whatsapp_schema_init'))"))

    try:
        Base.metadata.create_all(bind=connection)
        ensure_required_columns(connection)
    finally:
        if is_postgres:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext('ai_whatsapp_schema_init'))"))
