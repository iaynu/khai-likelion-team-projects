from typing import Optional, List, Tuple
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.store import Store


class StoreRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_source_id(self, store_source_id: str) -> Optional[Store]:
        result = await self.session.execute(
            select(Store).where(Store.store_source_id == store_source_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, store_id: str) -> Optional[Store]:
        result = await self.session.execute(
            select(Store).where(Store.id == store_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, store_name: str) -> Optional[Store]:
        result = await self.session.execute(
            select(Store).where(Store.store_name == store_name)
        )
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Store]:
        result = await self.session.execute(
            select(Store).order_by(Store.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        from sqlalchemy import func
        result = await self.session.execute(select(func.count(Store.id)))
        return result.scalar_one()

    def _name_filter(self, q: str):
        """PostgreSQL: ILIKE / SQLite: lower().like() — DB-agnostic name search."""
        term = f"%{q.lower()}%"
        return or_(
            func.lower(Store.store_name).like(term),
            func.lower(Store.area_code).like(term),
        )

    async def search(
        self,
        q: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Store]:
        stmt = select(Store)
        if q:
            stmt = stmt.where(self._name_filter(q))
        stmt = stmt.order_by(Store.store_name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_filtered(self, q: Optional[str] = None) -> int:
        stmt = select(func.count(Store.id))
        if q:
            stmt = stmt.where(self._name_filter(q))
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def upsert(
        self,
        store_source_id: str,
        store_name: str,
        sector_name: Optional[str] = None,
        area_code: Optional[str] = None,
    ) -> Tuple[Store, bool]:
        """Returns (store, created). created=True if new row inserted."""
        existing = await self.get_by_source_id(store_source_id)
        if existing:
            existing.store_name = store_name
            if sector_name is not None:
                existing.sector_name = sector_name
            if area_code is not None:
                existing.area_code = area_code
            await self.session.flush()
            return existing, False
        store = Store(
            store_source_id=store_source_id,
            store_name=store_name,
            sector_name=sector_name,
            area_code=area_code,
        )
        self.session.add(store)
        await self.session.flush()
        return store, True
