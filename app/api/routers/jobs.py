"""
Jobs API - 비동기 작업 관리
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.job import JobCreateRequest, JobOut, JobListOut
from app.repositories.job_repo import JobRepo
from app.models.job import JobStatus
from tasks.x_report_task import generate_x_report
from app.core.config import settings
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusUpdate(BaseModel):
    status: str
    error_message: Optional[str] = None  # FAILED 상태 시 에러 메시지


def verify_worker_api_key(x_api_key: str = Header(...)) -> None:
    """Worker API Key 검증"""
    if x_api_key != settings.WORKER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")


@router.post("", response_model=JobOut, status_code=201)
async def create_job(body: JobCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    비동기 작업 생성 및 큐에 등록
    X-Report, Y-Report, Simulation 등의 작업을 등록합니다.

    작업은 즉시 Job Queue에 등록되고 Worker가 처리합니다.
    응답으로 받은 job_id로 작업 상태를 확인할 수 있습니다.
    """
    from app.repositories.store_repo import StoreRepo

    repo = JobRepo(db)
    store_repo = StoreRepo(db)

    # Store 조회 (이름 가져오기)
    store = await store_repo.get_by_source_id(body.store_source_id)
    if not store:
        raise HTTPException(
            status_code=404,
            detail={"code": "STORE_NOT_FOUND", "message": f"store_source_id={body.store_source_id}"}
        )

    # Job 생성 (celery_task_id는 아직 없음)
    job = await repo.create(
        job_type=body.job_type,
        celery_task_id=None,  # 먼저 None으로 생성
        store_source_id=body.store_source_id,
        snapshot_version=body.snapshot_version,
        prompt_id=body.prompt_id,
    )

    await db.commit()
    await db.refresh(job)

    # Celery Task 등록
    if body.job_type == "x_report":
        celery_task = generate_x_report.delay(
            job_id=job.id,
            store_id=store.id,
            store_source_id=body.store_source_id,
            store_name=store.store_name,
            snapshot_version=body.snapshot_version,
            prompt_id=body.prompt_id,
        )

        # Job에 celery_task_id 업데이트
        job.celery_task_id = celery_task.id
        await db.commit()
        await db.refresh(job)

    logger.info(f"Job created: id={job.id}, type={body.job_type}, celery_task_id={job.celery_task_id}")
    return JobOut.model_validate(job, from_attributes=True)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """
    작업 상태 조회

    작업의 현재 상태, 진행률, 결과 ID 등을 확인합니다.
    - status: pending, processing, completed, failed
    - progress: 0-100
    - result_id: 완료된 경우 x_report_id or y_report_id
    """
    repo = JobRepo(db)
    job = await repo.get_by_id(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": f"job_id={job_id}"}
        )

    return JobOut.model_validate(job, from_attributes=True)


@router.patch("/{job_id}/status", response_model=JobOut, dependencies=[Depends(verify_worker_api_key)])
async def update_job_status(
    job_id: int,
    body: JobStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    작업 상태 업데이트 (Worker 전용)

    Worker가 작업 처리 중 상태를 업데이트합니다.
    status가 'failed'인 경우 error_message도 함께 업데이트합니다.
    """
    repo = JobRepo(db)
    job = await repo.get_by_id(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": f"job_id={job_id}"}
        )

    # JobStatus enum으로 변환
    try:
        new_status = JobStatus(body.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_STATUS", "message": f"Invalid status: {body.status}"}
        )

    # 상태 업데이트 (error_message 포함)
    job = await repo.update_status(
        job_id,
        new_status,
        error_message=body.error_message
    )
    await db.commit()
    await db.refresh(job)

    if new_status == JobStatus.FAILED:
        logger.error(f"Job {job_id} status updated to FAILED: {body.error_message[:100] if body.error_message else 'No error message'}")
    else:
        logger.info(f"Job {job_id} status updated to {new_status}")

    return JobOut.model_validate(job, from_attributes=True)


@router.get("", response_model=JobListOut)
async def list_jobs(
    status: Optional[JobStatus] = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    작업 목록 조회

    status 파라미터로 특정 상태의 작업만 필터링 가능:
    - pending: 대기 중
    - processing: 처리 중
    - completed: 완료
    - failed: 실패
    """
    repo = JobRepo(db)

    if status:
        items = await repo.list_by_status(status, limit=limit)
        total = len(items)  # 간단한 구현 (정확한 total은 별도 count 쿼리 필요)
    else:
        items = await repo.list_all(limit=limit, offset=offset)
        total = await repo.count()

    return JobListOut(
        items=[JobOut.model_validate(j, from_attributes=True) for j in items],
        total=total,
    )
