from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from backend.config import settings

# Async engine — with connection pool tuning
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,       # detect stale connections
    pool_recycle=1800,         # recycle connections every 30 min
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# Sync engine (for Alembic and ETL)
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SyncSessionLocal = sessionmaker(sync_engine)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Get async DB session (no RLS context)."""
    async with AsyncSessionLocal() as session:
        yield session


async def get_db_with_rls(project_id: int) -> AsyncSession:
    """
    Get async DB session with RLS context set.

    Sets SET LOCAL app.project_id = :pid so that PostgreSQL RLS policies
    filter rows automatically. SET LOCAL is transaction-scoped, so it
    doesn't leak to other requests sharing the same connection.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.project_id = :pid"),
                {"pid": str(project_id)},
            )
            yield session

