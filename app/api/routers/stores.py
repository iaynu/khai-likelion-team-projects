import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.store_repo import StoreRepo
from app.repositories.store_document_repo import StoreDocumentRepo
from app.schemas.store import StoreOut, StoreListOut, StoreDocumentOut, StoreDocumentListOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=StoreListOut)
async def list_stores(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    repo = StoreRepo(db)
    items = await repo.list_all(limit=limit, offset=offset)
    total = await repo.count()
    return StoreListOut(
        items=[StoreOut.model_validate(s, from_attributes=True) for s in items],
        total=total,
    )


@router.get("/{store_id}", response_model=StoreOut)
async def get_store(store_id: str, db: AsyncSession = Depends(get_db)):
    repo = StoreRepo(db)
    store = await repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"code": "STORE_NOT_FOUND", "message": f"store_id={store_id}"})
    return StoreOut.model_validate(store, from_attributes=True)


@router.get("/{store_id}/documents", response_model=StoreDocumentListOut)
async def list_documents(store_id: str, db: AsyncSession = Depends(get_db)):
    store_repo = StoreRepo(db)
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise HTTPException(status_code=404, detail={"code": "STORE_NOT_FOUND", "message": f"store_id={store_id}"})

    doc_repo = StoreDocumentRepo(db)
    items = await doc_repo.list_by_store(store_id)
    total = await doc_repo.count_by_store(store_id)
    return StoreDocumentListOut(
        items=[StoreDocumentOut.model_validate(d, from_attributes=True) for d in items],
        total=total,
    )
