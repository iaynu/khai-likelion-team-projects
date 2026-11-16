from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.store_document import StoreDocument


class StoreDocumentRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, store_id: str, snapshot_id: int, doc_type: str) -> Optional[StoreDocument]:
        result = await self.session.execute(
            select(StoreDocument).where(
                StoreDocument.store_id == store_id,
                StoreDocument.snapshot_id == snapshot_id,
                StoreDocument.doc_type == doc_type,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_store(self, store_id: str, limit: int = 100) -> List[StoreDocument]:
        result = await self.session.execute(
            select(StoreDocument)
            .where(StoreDocument.store_id == store_id)
            .order_by(StoreDocument.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_store(self, store_id: str) -> int:
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.count(StoreDocument.id)).where(StoreDocument.store_id == store_id)
        )
        return result.scalar_one()

    async def upsert(
        self, store_id: str, snapshot_id: int, doc_type: str, payload: dict
    ) -> Tuple[StoreDocument, bool]:
        existing = await self.get(store_id, snapshot_id, doc_type)
        if existing:
            existing.payload = payload
            await self.session.flush()
            return existing, False
        doc = StoreDocument(
            store_id=store_id,
            snapshot_id=snapshot_id,
            doc_type=doc_type,
            payload=payload,
        )
        self.session.add(doc)
        await self.session.flush()
        return doc, True
