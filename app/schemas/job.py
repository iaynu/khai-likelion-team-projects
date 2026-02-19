"""
Job 스키마 - 작업 관리 API용 스키마
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from app.models.job import JobType, JobStatus


# ──────────────────────────────────────────────────
# Request Schemas
# ──────────────────────────────────────────────────

class JobCreateRequest(BaseModel):
    """작업 생성 요청 (X-Report, Y-Report 등)"""
    job_type: JobType
    store_source_id: Optional[str] = None
    snapshot_version: str = "v1"
    prompt_id: str = "default"

    class Config:
        json_schema_extra = {
            "example": {
                "job_type": "x_report",
                "store_source_id": "1254472158",
                "snapshot_version": "v1",
                "prompt_id": "default"
            }
        }


# ──────────────────────────────────────────────────
# Response Schemas
# ──────────────────────────────────────────────────

class JobOut(BaseModel):
    """작업 상태 응답"""
    id: int
    celery_task_id: Optional[str]
    job_type: JobType
    status: JobStatus
    store_source_id: Optional[str]
    snapshot_version: Optional[str]
    prompt_id: Optional[str]
    result_id: Optional[int]  # x_report_id or y_report_id
    error_message: Optional[str]
    progress: int
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobListOut(BaseModel):
    """작업 목록 응답"""
    items: List[JobOut]
    total: int
