"""
Job 스키마 - 작업 관리 API용 스키마
"""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict

from app.models.job import JobType, JobStatus


# ──────────────────────────────────────────────────
# Request Schemas
# ──────────────────────────────────────────────────

class JobCreateRequest(BaseModel):
    """작업 생성 요청 (X-Report, Y-Report 등)"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_type": "x_report",
                "store_source_id": "1254472158",
                "snapshot_version": "v1",
                "prompt_id": "default"
            }
        }
    )

    job_type: JobType
    store_source_id: Optional[str] = None
    snapshot_version: str = "v1"
    prompt_id: str = "default"


# ──────────────────────────────────────────────────
# Response Schemas
# ──────────────────────────────────────────────────

class JobOut(BaseModel):
    """GET /jobs/{id} 응답 — 프론트 폴링용

    필수 필드:
      status       — 현재 작업 상태. "pending"|"processing"|"completed"|"failed"
      result_id    — completed 시 결과 레코드 PK (sim_id 또는 y_report_id), 없으면 null
      error_message — failed 시 [ERROR_CODE] prefix 포함 전문, 없으면 null

    선택 필드:
      progress     — 0–100, 미지원 구간에서는 null
    """
    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,   # Enum → .value 문자열 (프론트 안전)
        json_schema_extra={
            "examples": {
                "completed": {
                    "summary": "completed 케이스",
                    "value": {
                        "id": 1,
                        "celery_task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "job_type": "simulation",
                        "status": "completed",
                        "store_source_id": "돼지야",
                        "snapshot_version": None,
                        "prompt_id": None,
                        "result_id": 7,
                        "error_message": None,
                        "progress": 100,
                        "created_at": "2026-02-23T10:00:00",
                        "updated_at": "2026-02-23T10:05:00",
                        "started_at": "2026-02-23T10:00:01",
                        "completed_at": "2026-02-23T10:05:00"
                    }
                },
                "failed": {
                    "summary": "failed 케이스",
                    "value": {
                        "id": 2,
                        "celery_task_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                        "job_type": "y_report",
                        "status": "failed",
                        "store_source_id": "돼지야",
                        "snapshot_version": None,
                        "prompt_id": None,
                        "result_id": None,
                        "error_message": "[BACKEND_TIMEOUT] 시뮬레이션 시간 초과 | Context: store=돼지야, sim_id=3, job_id=2",
                        "progress": 0,
                        "created_at": "2026-02-23T11:00:00",
                        "updated_at": "2026-02-23T11:01:00",
                        "started_at": "2026-02-23T11:00:01",
                        "completed_at": None
                    }
                }
            }
        }
    )

    id: int
    celery_task_id: Optional[str] = None
    job_type: str                         # "x_report"|"y_report"|"simulation"
    status: str                           # "pending"|"processing"|"completed"|"failed"
    store_source_id: Optional[str] = None
    snapshot_version: Optional[str] = None
    prompt_id: Optional[str] = None
    result_id: Optional[int] = None       # completed 시 sim_id 또는 y_report_id
    error_message: Optional[str] = None   # failed 시 [ERROR_CODE] prefix 포함
    progress: Optional[int] = None        # 0–100, 미지원 구간에서는 null
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class JobListOut(BaseModel):
    """작업 목록 응답"""
    items: List[JobOut]
    total: int
