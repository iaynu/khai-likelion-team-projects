"""
SQLite 데이터베이스 초기화 스크립트
테이블을 생성합니다.
"""
import asyncio
from app.db.base import Base, get_engine
import app.models  # noqa: F401 — 모든 모델 등록


async def init_db():
    """Create all tables in SQLite database."""
    engine = get_engine()

    async with engine.begin() as conn:
        # Drop all tables (fresh start)
        await conn.run_sync(Base.metadata.drop_all)
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("✅ Database tables created successfully!")


if __name__ == "__main__":
    asyncio.run(init_db())
