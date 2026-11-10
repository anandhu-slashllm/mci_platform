from collections.abc import AsyncGenerator
from uuid import uuid4

from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()


def _engine_kwargs() -> dict:
    if settings.db_pool_mode == "serverless":
        # Each function instance is short-lived and Supabase's pooler (Supavisor, transaction mode)
        # does the pooling, so: no client-side pool, and no server-side prepared statements
        # (they don't survive across pooled transactions).
        return {
            "poolclass": NullPool,
            "connect_args": {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
            },
        }
    return {"pool_pre_ping": True}


engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs())
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
