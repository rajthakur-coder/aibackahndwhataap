from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

DATABASE_URL = settings.database_url
ASYNC_DATABASE_URL = (
    DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    if DATABASE_URL.startswith("postgresql://")
    else DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()
_async_session_local = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    global _async_session_local
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    if _async_session_local is None:
        async_engine = create_async_engine(
            ASYNC_DATABASE_URL,
            pool_pre_ping=True,
        )
        _async_session_local = async_sessionmaker(
            bind=async_engine,
            expire_on_commit=False,
        )
    async with _async_session_local() as db:
        yield db
