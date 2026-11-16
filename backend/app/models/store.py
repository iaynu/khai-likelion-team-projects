import uuid as _uuid

from sqlalchemy import Column, String, DateTime, func
from app.db.base import Base


class Store(Base):
    __tablename__ = "stores"

    # id stored as VARCHAR(36) in ORM — migration uses native UUID on Postgres
    id = Column(String(36), primary_key=True, default=lambda: str(_uuid.uuid4()))
    store_source_id = Column(String(50), unique=True, nullable=False, index=True)
    store_name = Column(String(200), nullable=False)
    sector_name = Column(String(100), nullable=True)
    area_code = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
