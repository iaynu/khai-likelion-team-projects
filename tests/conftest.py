"""
Test fixtures: in-memory async SQLite DB with all tables created from ORM metadata.
Patches app.db.session.get_db to yield the test session.
"""
import pytest
import pytest_asyncio

# ── 1) Patch settings BEFORE any app imports ────────────────
import app.core.config as _cfg
_cfg.settings.DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# ── 2) Now safe to import app modules ────────────────────────
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

import app.models  # noqa: F401  — registers models on Base.metadata
from app.db.base import Base, reset_engine

# reset any cached engine so get_engine() picks up the patched URL
reset_engine()

from app.main import app  # noqa: E402
from app.db import session as _session_mod  # noqa: E402

# ── Shared test engine (single in-memory DB across a test) ───
TEST_ENGINE = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestingSessionLocal = async_sessionmaker(TEST_ENGINE, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create all tables before each test, drop after."""
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture()
def db_session():
    """Return a raw async session for direct assertions."""
    return TestingSessionLocal()


@pytest.fixture(autouse=True)
def override_get_db():
    """Override FastAPI's get_db dependency with test session."""
    async def _get_db():
        async with TestingSessionLocal() as session:
            yield session

    app.dependency_overrides[_session_mod.get_db] = _get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")
