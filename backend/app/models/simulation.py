"""
Simulation 모델 - 전략 전/후 시뮬레이션 결과
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from sqlalchemy.sql import func

from app.db.base import Base


class Simulation(Base):
    """
    시뮬레이션 실행 결과 테이블.

    하나의 레코드 = 전략 전(before) + 전략 후(after) 전체 파이프라인 실행 결과.
    selected_strategy_ids가 빈 리스트이면 before-only 실행.
    """
    __tablename__ = "simulations"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 연결 정보
    store_source_id = Column(String(100), nullable=False, index=True)  # 예: "돼지야"
    x_report_id = Column(Integer, nullable=True, index=True)           # FK to x_reports

    # 입력 파라미터
    selected_strategy_ids = Column(JSON, nullable=False, default=list)  # ["S1_A", "S2_A"]
    days = Column(Integer, nullable=False, default=7)
    seed = Column(Integer, nullable=False, default=42)
    settings_json = Column(JSON, nullable=True)  # agent_count 등 추가 설정

    # 입력/출력 원본
    input_json = Column(JSON, nullable=True)   # 요청 전체 파라미터 스냅샷
    output_json = Column(JSON, nullable=True)  # before/after 요약 통계

    # 상태 및 타임스탬프
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING/COMPLETED/FAILED
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    def __repr__(self):
        return f"<Simulation(id={self.id}, store={self.store_source_id}, status={self.status})>"
