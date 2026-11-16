import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.store_repo import StoreRepo
from app.repositories.store_document_repo import StoreDocumentRepo
from app.schemas.store import (
    StoreOut, StoreListOut,
    StoreDocumentOut, StoreDocumentListOut,
    StoreListViewOut,
)
from app.services.store_service import list_stores_view

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/stores", tags=["stores"])

_MAX_LIMIT = 200


@router.get("", response_model=StoreListViewOut)
async def list_stores(
    q: Optional[str] = Query(default=None, description="name/area 부분 검색"),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT, description="페이지 크기 (최대 200)"),
    offset: int = Query(default=0, ge=0, description="오프셋"),
    sort: Optional[str] = Query(default="name", description="정렬 기준 (현재 name만 지원)"),
    db: AsyncSession = Depends(get_db),
):
    """매장 목록 조회 (프론트 View DTO).

    - `q`: store_name / area_code 부분 검색 (대소문자 무시)
    - `limit`: 1–200
    - `offset`: ≥ 0
    - lat/lng가 DB에 없을 경우 80% 이상 null → 전체 포함 + 서버 경고 로그
    """
    clamped_limit = min(limit, _MAX_LIMIT)
    return await list_stores_view(session=db, q=q, limit=clamped_limit, offset=offset)


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
