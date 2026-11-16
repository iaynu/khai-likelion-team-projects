from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.db.base import get_engine


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields a single session per request."""
    factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@asynccontextmanager
async def get_async_session():
    """
    Context manager for direct async session access (used by Celery tasks).

    Usage:
        async with get_async_session() as session:
            async with session.begin():
                # DB operations here
    """
    factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
