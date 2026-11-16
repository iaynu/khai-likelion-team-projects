from sqlalchemy import Column, Integer, String, Text, DateTime, func, UniqueConstraint, JSON
from app.db.base import Base


class XReport(Base):
    __tablename__ = "x_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(36), nullable=False)          # UUID as string
    snapshot_id = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    content_text = Column(Text, nullable=True)
    config_json = Column(JSON, nullable=True)
    prompt_id = Column(String(80), nullable=False)
    model_meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("store_id", "snapshot_id", "prompt_id", name="uq_xreport"),
    )
