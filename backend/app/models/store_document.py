from sqlalchemy import Column, Integer, String, DateTime, func, UniqueConstraint, JSON
from app.db.base import Base


class StoreDocument(Base):
    __tablename__ = "store_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(36), nullable=False)          # UUID as string — portable
    snapshot_id = Column(Integer, nullable=False)
    doc_type = Column(String(30), nullable=False)          # 'MARKET_SALES_REVIEW' | 'POPULATION'
    payload = Column(JSON, nullable=False)                 # JSON works on both PG and SQLite
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("store_id", "snapshot_id", "doc_type", name="uq_store_doc"),
    )
