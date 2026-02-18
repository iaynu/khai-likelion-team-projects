from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


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
