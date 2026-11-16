from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None  # type: AsyncEngine | None


def get_engine() -> AsyncEngine:
    """Lazy engine factory — reads DATABASE_URL at first call, not at import time."""
    global _engine
    if _engine is None:
        from app.core.config import settings
        _engine = create_async_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            echo=settings.SQL_ECHO,  # Use SQL_ECHO instead of DEBUG for query logging
        )
    return _engine


def reset_engine() -> None:
    """Allow tests to swap the engine after patching settings."""
    global _engine
    _engine = None
