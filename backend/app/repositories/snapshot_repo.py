from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.data_snapshot import DataSnapshot


class SnapshotRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_version(self, version: str) -> Optional[DataSnapshot]:
        result = await self.session.execute(
            select(DataSnapshot).where(DataSnapshot.version == version)
        )
        return result.scalar_one_or_none()

    async def create(self, version: str, source_hash: Optional[str] = None) -> DataSnapshot:
        snap = DataSnapshot(version=version, source_hash=source_hash)
        self.session.add(snap)
        await self.session.flush()
        return snap
