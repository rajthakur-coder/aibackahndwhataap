from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings
from app.shared.tenant import current_tenant_id


def _async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite:///"):
        return database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return database_url


def _sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://") or database_url.startswith("postgresql+asyncpg://"):
        url = make_url(
            database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )
        query = dict(url.query)
        if query.get("ssl") == "require":
            query.pop("ssl", None)
            query["sslmode"] = "require"
        return url.set(query=query).render_as_string(hide_password=False)
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if database_url.startswith("sqlite+aiosqlite:///"):
        return database_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    return database_url


Base = declarative_base()
ASYNC_DATABASE_URL = _async_database_url(settings.DATABASE_URI)

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

sync_engine = create_engine(
    _sync_database_url(settings.DATABASE_URI),
    echo=settings.DEBUG,
    pool_pre_ping=True,
    future=True,
)

# Compatibility only: the request path uses AsyncSessionLocal/get_db, while the
# WhatsApp ARQ worker still needs a sync session until its processor is fully
# converted to async SQLAlchemy.
SessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


def fill_tenant_id_for_new_objects(session) -> None:
    tenant_id = current_tenant_id()
    if not tenant_id:
        return
    for obj in session.new:
        if hasattr(obj, "tenant_id") and not getattr(obj, "tenant_id", None):
            setattr(obj, "tenant_id", tenant_id)


@event.listens_for(SyncSession, "before_flush")
def _fill_tenant_id(session, flush_context, instances) -> None:
    fill_tenant_id_for_new_objects(session)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
