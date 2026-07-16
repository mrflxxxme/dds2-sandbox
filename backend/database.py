import asyncio
import logging

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.config import settings

# Async engine — with connection pool tuning (via PgBouncer)
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # detect stale connections
    pool_recycle=1800,  # recycle connections every 30 min
    pool_timeout=5,  # wait max 5s for a connection (fail fast)
    connect_args={
        "command_timeout": 60,
    },
)

_pool_logger = logging.getLogger("dds.pool")


# Set statement_timeout on each connection checkout (PgBouncer-compatible)
@event.listens_for(async_engine.sync_engine, "connect")
def set_statement_timeout(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET statement_timeout = '60000'")
    cursor.close()


@event.listens_for(async_engine.sync_engine, "checkout")
def _on_checkout(dbapi_connection, connection_record, connection_proxy):
    pool = async_engine.pool
    _pool_logger.debug(
        "pool checkout: size=%d checked_out=%d overflow=%d",
        pool.size(),
        pool.checkedout(),
        pool.overflow(),
    )


@event.listens_for(async_engine.sync_engine, "checkin")
def _on_checkin(dbapi_connection, connection_record):
    pool = async_engine.pool
    if pool.checkedout() > pool.size():
        _pool_logger.warning(
            "pool high usage on checkin: checked_out=%d size=%d overflow=%d",
            pool.checkedout(),
            pool.size(),
            pool.overflow(),
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
    pool_timeout=10,  # wait max 10s for a connection from pool
)
SyncSessionLocal = sessionmaker(sync_engine)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """Get async DB session (no RLS context).

    Тердаун под `asyncio.shield`: когда клиент рвёт запрос (перезагрузка тяжёлой
    страницы), отмена таска прилетает и в cleanup — незащищённый `close()`
    прерывается ПЕРВЫМ же await и соединение возвращается в пул с открытой
    серверной транзакцией. Такие зомби (idle in transaction) выедают пул
    pgbouncer, и стенд встаёт колом (клин 2026-07-16: 20 зомби = весь пул,
    все запросы 60с+ таймаут). shield доводит rollback+close до конца.
    """
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await asyncio.shield(session.close())


async def get_db_with_rls(project_id: int) -> AsyncSession:
    """
    Get async DB session with RLS context set.

    Sets SET LOCAL app.project_id = :pid so that PostgreSQL RLS policies
    filter rows automatically. SET LOCAL is transaction-scoped, so it
    doesn't leak to other requests sharing the same connection.
    """
    async with AsyncSessionLocal() as session, session.begin():
        await session.execute(
            text("SET LOCAL app.project_id = :pid"),
            {"pid": str(project_id)},
        )
        yield session
