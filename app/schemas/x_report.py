from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class StrategySaveData(BaseModel):
    """Worker가 전송하는 Strategy 데이터"""
    solution_group: int
    action_idx: int
    title: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    impact_mapping: Optional[Dict[str, Any]] = None


class XReportSaveRequest(BaseModel):
    """Worker가 생성한 X-Report 저장 요청"""
    job_id: int  # 작업 ID
    store_source_id: str
    snapshot_version: str
    prompt_id: str
    content_text: str
    config_json: Optional[Dict[str, Any]] = None
    model_meta: Optional[Dict[str, Any]] = None
    strategies: List[StrategySaveData]  # 12개 전략

    class Config:
        json_schema_extra = {
            "example": {
                "job_id": 1,
                "store_source_id": "1254472158",
                "snapshot_version": "v1",
                "prompt_id": "default",
                "content_text": "Generated X-Report content...",
                "config_json": {"strategies": []},
                "model_meta": {"model": "gpt-4", "usage": {}},
                "strategies": [
                    {
                        "solution_group": 1,
                        "action_idx": 1,
                        "title": "Strategy title",
                        "description": "Strategy description",
                        "config": {},
                        "impact_mapping": {}
                    }
                ]
            }
        }


class XReportCreateRequest(BaseModel):
    store_source_id: Optional[str] = None
    store_name: Optional[str] = None
    snapshot_version: str
    prompt_id: str
    force_regenerate: bool = False


# ── X-Report View DTO (BACKEND-2 전용) ────────────────────────────────────────

class RadarDataItem(BaseModel):
    """레이더 차트 한 축의 데이터 (Recharts 형식)"""
    subject: str
    A: int = Field(..., alias="A")      # Recharts 데이터 키 — 대문자 유지
    fullMark: int = 100
    reason: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class KeywordItem(BaseModel):
    """키워드 클라우드 한 항목"""
    text: str
    sentiment: str  # "neutral" | "positive" | "negative"


class SolutionItem(BaseModel):
    """전략 솔루션 한 항목"""
    category: str
    title: str
    desc: str
    execution: str
    effect: str = ""


class XReportViewOut(BaseModel):
    """프론트 X-Report View DTO — camelCase 출력 (BACKEND-2 전용)"""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    id: int
    store_id: str
    name: Optional[str] = None          # store_name — view service 에서 조회
    status: str
    grade: Optional[str] = None
    rank_percent: Optional[int] = None
    radar_data: List[RadarDataItem] = Field(default_factory=list)
    keywords: List[KeywordItem] = Field(default_factory=list)
    solutions: List[SolutionItem] = Field(default_factory=list)
    description: Optional[str] = None
    full_report: Optional[str] = None


class XReportOut(BaseModel):
    id: int
    store_id: str
    snapshot_id: int
    status: str
    content_text: Optional[str] = None
    config_json: Optional[dict] = None
    prompt_id: str
    model_meta: Optional[dict] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    cached: Optional[bool] = None  # True=캐시 반환, False=신규/재생성, None=정보 없음
