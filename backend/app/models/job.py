"""
Job 모델 - 비동기 작업 관리
Worker가 처리할 작업을 추적합니다.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SQLEnum
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class JobType(str, enum.Enum):
    """작업 타입"""
    X_REPORT = "x_report"
    Y_REPORT = "y_report"
    SIMULATION = "simulation"


class JobStatus(str, enum.Enum):
    """작업 상태"""
    PENDING = "pending"        # 대기 중
    PROCESSING = "processing"  # 처리 중
    COMPLETED = "completed"    # 완료
    FAILED = "failed"          # 실패


class Job(Base):
    """
    비동기 작업 테이블
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Celery Task ID
    celery_task_id = Column(String(255), unique=True, nullable=True, index=True)

    # 작업 정보
    job_type = Column(SQLEnum(JobType), nullable=False, index=True)
    status = Column(SQLEnum(JobStatus), nullable=False, default=JobStatus.PENDING, index=True)

    # 작업 파라미터 (JSON 문자열)
    store_source_id = Column(String(50), nullable=True, index=True)
    snapshot_version = Column(String(50), nullable=True)
    prompt_id = Column(String(100), nullable=True)

    # 결과
    result_id = Column(Integer, nullable=True)  # x_report.id or y_report.id
    error_message = Column(Text, nullable=True)

    # 진행률 (0-100)
    progress = Column(Integer, default=0)

    # 메타데이터
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Job(id={self.id}, type={self.job_type}, status={self.status})>"
