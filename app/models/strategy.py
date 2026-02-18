from sqlalchemy import Column, Integer, Text, UniqueConstraint, JSON
from app.db.base import Base


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    x_report_id = Column(Integer, nullable=False)
    solution_group = Column(Integer, nullable=False)   # 1~3
    action_idx = Column(Integer, nullable=False)       # 1~4
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    config = Column(JSON, nullable=True)
    impact_mapping = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("x_report_id", "solution_group", "action_idx", name="uq_strategy"),
    )
