from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel


class StrategySaveData(BaseModel):
    """Worker가 전송하는 Strategy 데이터"""
    solution_group: int
    action_idx: int
    title: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    impact_mapping: Optional[Dict[str, Any]] = None


class XReportSaveRequest(BaseModel):
    """Worker가 생성한 X-Report 저장 요청"""
    job_id: int  # 작업 ID
    store_source_id: str
    snapshot_version: str
    prompt_id: str
    content_text: str
    config_json: Optional[Dict[str, Any]] = None
    model_meta: Optional[Dict[str, Any]] = None
    strategies: List[StrategySaveData]  # 12개 전략

    class Config:
        json_schema_extra = {
            "example": {
                "job_id": 1,
                "store_source_id": "1254472158",
                "snapshot_version": "v1",
                "prompt_id": "default",
                "content_text": "Generated X-Report content...",
                "config_json": {"strategies": []},
                "model_meta": {"model": "gpt-4", "usage": {}},
                "strategies": [
                    {
                        "solution_group": 1,
                        "action_idx": 1,
                        "title": "Strategy title",
                        "description": "Strategy description",
                        "config": {},
                        "impact_mapping": {}
                    }
                ]
            }
        }


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
