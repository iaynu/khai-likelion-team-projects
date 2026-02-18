from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class XReportCreateRequest(BaseModel):
    store_source_id: Optional[str] = None
    store_name: Optional[str] = None
    snapshot_version: str
    prompt_id: str
    force_regenerate: bool = False


class XReportOut(BaseModel):
    id: int
    store_id: str
    snapshot_id: int
    status: str
    content_text: Optional[str] = None
    config_json: Optional[dict] = None
    prompt_id: str
    model_meta: Optional[dict] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
