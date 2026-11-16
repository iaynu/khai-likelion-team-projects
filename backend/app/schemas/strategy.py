from typing import Optional, List
from pydantic import BaseModel


class StrategyOut(BaseModel):
    id: int
    x_report_id: int
    solution_group: int
    action_idx: int
    title: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None
    impact_mapping: Optional[dict] = None


class StrategyListOut(BaseModel):
    items: List[StrategyOut]
    total: int
