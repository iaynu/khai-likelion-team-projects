"""
Simulation Pydantic 스키마
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


class SimulationCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "store_source_id": "돼지야",
                "x_report_id": None,
                "selected_strategy_ids": ["S1_A", "S2_A"],
                "days": 7,
                "seed": 42,
                "agent_count": 160,
                "max_concurrent_llm_calls": 5,
            }
        }
    )

    store_source_id: str = Field(..., description="타겟 매장명 (예: '돼지야')")
    x_report_id: Optional[int] = Field(None, description="연결된 X-Report ID")
    selected_strategy_ids: List[str] = Field(
        default_factory=list,
        description="X_to_Sim 형식 전략 ID 목록 (예: ['S1_A', 'S2_A']). 빈 리스트이면 전략 미적용",
    )
    days: int = Field(default=7, ge=1, le=365, description="시뮬레이션 기간 (일)")
    seed: int = Field(default=42, description="랜덤 시드")
    agent_count: int = Field(default=160, ge=1, le=160, description="에이전트 수")
    max_concurrent_llm_calls: int = Field(
        default=5, ge=1, le=20, description="동시 LLM 호출 수 상한 (기본 5)"
    )


class SimulationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_source_id: str
    x_report_id: Optional[int] = None
    selected_strategy_ids: List[str]
    days: int
    seed: int
    status: str
    output_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    @computed_field  # type: ignore[misc]
    @property
    def summary(self) -> Optional[Dict[str, Any]]:
        """output_json.meta에서 주요 실행 정보를 추출한다 (COMPLETED일 때만 값 있음)."""
        if not self.output_json:
            return None
        meta = self.output_json.get("meta", {})
        if not meta:
            return None
        keys = ("timing_ms", "agent_count", "days", "engine", "run_id")
        extracted = {k: meta[k] for k in keys if k in meta}
        return extracted or None


class SimulationJobResponse(BaseModel):
    """POST /simulations 응답: job 생성 결과"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": 1,
                "sim_id": 1,
                "status": "pending",
            }
        }
    )

    job_id: int
    sim_id: int
    status: str = "pending"


class SimulationListResponse(BaseModel):
    """GET /stores/{store_source_id}/simulations 응답"""

    items: List[SimulationResponse]
    total: int
