from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StoreOut(BaseModel):
    id: str
    store_source_id: str
    store_name: str
    sector_name: Optional[str] = None
    area_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class StoreListOut(BaseModel):
    items: List[StoreOut]
    total: int


class StoreListItemOut(BaseModel):
    """프론트 매장 목록 View DTO — 필드명이 프론트 contract와 정확히 일치 (BACKEND-1 전용)

    Service 레이어에서 수동으로 구성한다 (from_attributes 미사용).
    id       = Store.store_source_id
    name     = Store.store_name
    address  = Store.area_code (fallback; 좌표 확보 후 실제 주소로 교체 예정)
    lat/lng  = StoreDocument.payload 파싱 (없으면 null)
    sentiment/grade/rankPercent = StoreDocument.payload 또는 XReport 파싱
    """

    id: str
    name: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    sentiment: Optional[float] = None
    revenue: Optional[int] = None
    grade: Optional[str] = None
    rankPercent: Optional[int] = None  # camelCase 직접 정의 (alias_generator 불필요)


class StoreListViewOut(BaseModel):
    """프론트 매장 목록 View 응답 — pagination 포함 (BACKEND-1 전용)"""

    items: List[StoreListItemOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class StoreDocumentOut(BaseModel):
    id: int
    store_id: str
    snapshot_id: int
    doc_type: str
    payload: dict
    created_at: Optional[datetime] = None


class StoreDocumentListOut(BaseModel):
    items: List[StoreDocumentOut]
    total: int
