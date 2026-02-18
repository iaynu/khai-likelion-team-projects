from sqlalchemy import Column, Integer, BigInteger, String, DateTime, func
from app.db.base import Base


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(50), unique=True, nullable=False, index=True)
    source_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
