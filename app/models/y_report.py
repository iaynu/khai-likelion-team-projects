"""
YReport 모델 - 전략 전/후 비교 분석 보고서
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.sql import func

from app.db.base import Base


class YReport(Base):
    """
    비교 분석 보고서 테이블.

    하나의 레코드 = 특정 시뮬레이션(simulation_id)의
    before/after 비교 보고서 생성 결과.
    """
    __tablename__ = "y_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 연결 정보
    store_source_id = Column(String(100), nullable=False, index=True)
    x_report_id = Column(Integer, nullable=True, index=True)
    simulation_id = Column(Integer, nullable=False, index=True)  # FK to simulations

    # 입력/출력 원본
    input_json = Column(JSON, nullable=True)   # before_dir, after_dir 등 요청 파라미터
    output_json = Column(JSON, nullable=True)  # metrics_summary dict
    markdown = Column(Text, nullable=True)     # 마크다운 보고서 전문

    # 상태 및 타임스탬프
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING/COMPLETED/FAILED
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    def __repr__(self):
        return f"<YReport(id={self.id}, simulation_id={self.simulation_id}, status={self.status})>"
