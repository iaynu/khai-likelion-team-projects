"""
Jobs API - 비동기 작업 관리
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.job import JobCreateRequest, JobOut, JobListOut
from app.repositories.job_repo import JobRepo
from app.models.job import JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
async def create_job(body: JobCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    비동기 작업 생성 및 큐에 등록
    X-Report, Y-Report, Simulation 등의 작업을 등록합니다.

    작업은 즉시 Job Queue에 등록되고 Worker가 처리합니다.
    응답으로 받은 job_id로 작업 상태를 확인할 수 있습니다.
    """
    repo = JobRepo(db)

    # TODO: Celery task 등록 (Phase 2에서 구현)
    # celery_task = tasks.generate_x_report.delay(...)
    # celery_task_id = celery_task.id

    # 임시로 celery_task_id 없이 Job 생성
    job = await repo.create(
        job_type=body.job_type,
        celery_task_id=None,  # Phase 2에서 실제 task_id 설정
        store_source_id=body.store_source_id,
        snapshot_version=body.snapshot_version,
        prompt_id=body.prompt_id,
    )

    await db.commit()
    await db.refresh(job)

    logger.info(f"Job created: id={job.id}, type={body.job_type}")
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
