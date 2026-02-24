"""
YReport Pydantic 스키마
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field
from pydantic.alias_generators import to_camel


# ── Y-Report View DTO (BACKEND-3 전용) ────────────────────────────────────────
# 프론트 필드명이 이미 camelCase이므로 nested 모델은 alias_generator 없이 직접 정의.
# 상위 YReportViewOut만 alias_generator=to_camel 적용.

class YVisitStats(BaseModel):
    totalVisits: int
    marketShare: float


class YOverview(BaseModel):
    sim1: YVisitStats
    sim2: YVisitStats


class YKeywordItem(BaseModel):
    text: str
    weight: float


class YKeywords(BaseModel):
    sim1: List[YKeywordItem] = Field(default_factory=list)
    sim2: List[YKeywordItem] = Field(default_factory=list)


class YRatingPoint(BaseModel):
    score: float
    density: float


class YRatingSeries(BaseModel):
    sim1: List[YRatingPoint] = Field(default_factory=list)
    sim2: List[YRatingPoint] = Field(default_factory=list)


class YRatingDistribution(BaseModel):
    taste: YRatingSeries = Field(default_factory=YRatingSeries)
    value: YRatingSeries = Field(default_factory=YRatingSeries)
    atmosphere: YRatingSeries = Field(default_factory=YRatingSeries)


class YRatingSummaryItem(BaseModel):
    avg: float
    satisfaction: float


class YRatingSummary(BaseModel):
    sim1: YRatingSummaryItem
    sim2: YRatingSummaryItem


class YHourlyTrafficItem(BaseModel):
    slot: str
    sim1: int
    sim2: int


class YPeakSlot(BaseModel):
    sim1: str
    sim2: str


class YGenerationItem(BaseModel):
    gen: str
    sim1: int
    sim2: int


class YPurposeItem(BaseModel):
    type: str
    sim1Pct: float
    sim2Pct: float
    sim1Sat: float
    sim2Sat: float


class YRetention(BaseModel):
    sim1Agents: int
    sim2Agents: int
    retained: int
    newUsers: int
    churned: int
    retentionRate: float
    newRatio: float


class YRadarBenchmarkItem(BaseModel):
    metric: str
    unit: str
    targetBefore: float
    targetAfter: float
    comp1: float
    comp2: float
    comp3: float


class YRadarStores(BaseModel):
    comp1: str
    comp2: str
    comp3: str


class YAgentTypeItem(BaseModel):
    type: str
    sim1: int
    sim2: int


class YGenderItem(BaseModel):
    label: str
    sim1: int
    sim2: int


class YCrosstab(BaseModel):
    generations: List[str] = Field(default_factory=list)
    purposes: List[str] = Field(default_factory=list)
    sim2: List[List[float]] = Field(default_factory=list)


class YSideEffectItem(BaseModel):
    type: str   # "warning" | "watch"
    metric: str
    change: float
    unit: str
    detail: str


class YTradeoffItem(BaseModel):
    gain: str
    gainVal: str
    loss: str
    lossVal: str


class YRiskScore(BaseModel):
    score: int
    level: str           # "낮은 위험" | "중간 위험" | "높은 위험"
    positive: int
    watch: int
    negative: int
    totalMetrics: int


class YReportViewOut(BaseModel):
    """프론트 Y-Report View DTO — camelCase 출력 (BACKEND-3 전용)

    output_json 파싱 결과를 프론트 컴포넌트별 필드로 매핑한다.
    COMPLETED 아닌 경우 metric 필드는 null 또는 [].
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    id: int
    store_source_id: str
    simulation_id: int
    x_report_id: Optional[int] = None
    status: str

    # 지표 1: 방문 개요
    overview: Optional[YOverview] = None
    # 워드클라우드 키워드
    keywords: Optional[YKeywords] = None
    # 지표 2: 평점 분포 (KDE)
    rating_distribution: Optional[YRatingDistribution] = None
    rating_summary: Optional[YRatingSummary] = None
    # 지표 3: 시간대별 트래픽
    hourly_traffic: List[YHourlyTrafficItem] = Field(default_factory=list)
    peak_slot: Optional[YPeakSlot] = None
    # 지표 4: 세대별 증감
    generation: List[YGenerationItem] = Field(default_factory=list)
    # 지표 5: 방문 목적
    purpose: List[YPurposeItem] = Field(default_factory=list)
    # 지표 6: 재방문율
    retention: Optional[YRetention] = None
    # 지표 7: 경쟁사 벤치마크 레이더
    radar: List[YRadarBenchmarkItem] = Field(default_factory=list)
    radar_stores: Optional[YRadarStores] = None
    # 지표 9: 에이전트 유형
    agent_type: List[YAgentTypeItem] = Field(default_factory=list)
    # 지표 10: 성별 구성
    gender: List[YGenderItem] = Field(default_factory=list)
    # 지표 11: 세대×목적 크로스탭
    crosstab: Optional[YCrosstab] = None
    # 역효과 / 트레이드오프 / 리스크
    side_effects: List[YSideEffectItem] = Field(default_factory=list)
    tradeoffs: List[YTradeoffItem] = Field(default_factory=list)
    risk_score: Optional[YRiskScore] = None
    # 지표 8: LLM 종합 평가
    llm_summary: Optional[str] = None


class YReportCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "simulation_id": 1,
                "x_report_id": None,
                "options": {},
            }
        }
    )

    simulation_id: int = Field(..., description="비교 분석 대상 Simulation ID (COMPLETED 상태여야 함)")
    x_report_id: Optional[int] = Field(None, description="연결된 X-Report ID (선택)")
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "YReportAdapter 추가 옵션 (선택). "
            "before_dir / after_dir / target_store / output_dir 오버라이드 가능"
        ),
    )


class YReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_source_id: str
    simulation_id: int
    x_report_id: Optional[int] = None
    status: str
    output_json: Optional[Dict[str, Any]] = None
    markdown: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    @computed_field  # type: ignore[misc]
    @property
    def summary(self) -> Optional[Dict[str, Any]]:
        """output_json から 실행 메타와 핵심 KPI를 추출한다 (COMPLETED일 때만 값 있음).

        - service_meta: timing_ms, engine, run_id
        - structured: total_visits_before/after, visit_change_pct (엔진 출력에 따라 가변)
        """
        if not self.output_json:
            return None
        service_meta = self.output_json.get("service_meta", {})
        structured = self.output_json.get("structured", {})
        result: Dict[str, Any] = {}
        for k in ("timing_ms", "engine", "run_id"):
            if k in service_meta:
                result[k] = service_meta[k]
        for k in ("total_visits_before", "total_visits_after", "visit_change_pct"):
            if k in structured:
                result[k] = structured[k]
        return result or None


class YReportJobResponse(BaseModel):
    """POST /y-reports 응답: job 생성 결과"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": 2,
                "y_report_id": 1,
                "status": "pending",
            }
        }
    )

    job_id: int
    y_report_id: int
    status: str = "pending"


class YReportListResponse(BaseModel):
    """GET /stores/{store_source_id}/y-reports 응답"""

    items: List[YReportResponse]
    total: int
