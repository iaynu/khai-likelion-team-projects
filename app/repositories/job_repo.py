"""
Job Repository - 작업 관리 DB 접근
"""
from typing import Optional, List
from datetime import datetime
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobType, JobStatus


class JobRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        job_type: JobType,
        celery_task_id: Optional[str] = None,
        store_source_id: Optional[str] = None,
        snapshot_version: Optional[str] = None,
        prompt_id: Optional[str] = None,
    ) -> Job:
        """작업 생성"""
        job = Job(
            celery_task_id=celery_task_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            store_source_id=store_source_id,
            snapshot_version=snapshot_version,
            prompt_id=prompt_id,
            progress=0,
        )
        self.session.add(job)
        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def get_by_id(self, job_id: int) -> Optional[Job]:
        """ID로 조회"""
        stmt = select(Job).where(Job.id == job_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_celery_task_id(self, task_id: str) -> Optional[Job]:
        """Celery Task ID로 조회"""
        stmt = select(Job).where(Job.celery_task_id == task_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        job_id: int,
        status: JobStatus,
        progress: Optional[int] = None,
        error_message: Optional[str] = None,
        result_id: Optional[int] = None,
    ) -> Optional[Job]:
        """작업 상태 업데이트"""
        job = await self.get_by_id(job_id)
        if not job:
            return None

        job.status = status
        if progress is not None:
            job.progress = progress
        if error_message is not None:
            job.error_message = error_message
        if result_id is not None:
            job.result_id = result_id

        # 시작/완료 시간 자동 업데이트
        if status == JobStatus.PROCESSING and job.started_at is None:
            job.started_at = datetime.utcnow()
        if status in [JobStatus.COMPLETED, JobStatus.FAILED]:
            job.completed_at = datetime.utcnow()
            job.progress = 100 if status == JobStatus.COMPLETED else job.progress

        await self.session.flush()
        await self.session.refresh(job)
        return job

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Job]:
        """전체 작업 목록 (최신순)"""
        stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(self, status: JobStatus, limit: int = 100) -> List[Job]:
        """상태별 작업 목록"""
        stmt = select(Job).where(Job.status == status).order_by(Job.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        """전체 작업 수"""
        stmt = select(func.count()).select_from(Job)
        result = await self.session.execute(stmt)
        return result.scalar_one()
