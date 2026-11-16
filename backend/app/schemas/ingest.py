from typing import Optional, List, Any
from pydantic import BaseModel


class IngestRequest(BaseModel):
    snapshot_version: Optional[str] = None
    market_sales_review: Optional[List[Any]] = None
    population: Optional[List[Any]] = None


class IngestResponse(BaseModel):
    snapshot_version: str
    stores_inserted: int
    stores_updated: int
    documents_inserted: int
    documents_updated: int
