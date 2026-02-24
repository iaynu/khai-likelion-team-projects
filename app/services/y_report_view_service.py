"""
Y-Report View Service — GET /y-reports/{id}/view

데이터 파이프라인:
  1) YReportRepo.get_by_id()     → YReport ORM
  2) _parse_output_json()        → output_json dict
  3) _get_sections()             → metrics_summary 섹션 dict (sim_to_y 포맷 지원)
  4) _build_*() 헬퍼             → 각 DTO 섹션 조립
  5) YReportViewOut.model_validate() → 타입 검증 후 반환

output_json 구조 (실제 sim_to_y 출력):
  output_json = {
    "structured": {          ← metrics_summary (ComparisonReportGenerator)
      "overview":  {...},    ← analysis_1_overview()
      "rating":    {...},    ← analysis_2_rating_spread()
      "traffic":   {...},    ← analysis_3_hourly_traffic()
      "generation":{...},   ← analysis_4_generation_impact()
      "purpose":   {...},    ← analysis_5_purpose_analysis()
      "retention": {...},    ← analysis_6_retention()
      "radar":     {...},    ← analysis_7_radar_chart()
      "agent_type":{...},   ← analysis_9_agent_type()
      "gender":    {...},    ← analysis_10_gender_composition()
      "crosstab":  {...},    ← analysis_11_crosstab_heatmap()
      "safety":    {...},    ← analysis_safety_diagnosis()
      "tradeoff":  {...},    ← analysis_tradeoff()
    },
    "adapter_meta": {...},
    "service_meta": {...},
  }

sim1 = 기준(before), sim2 = 전략 적용(after)
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.y_report import YReport
from app.repositories.y_report_repo import YReportRepo
from app.schemas.y_report import (
    YAgentTypeItem,
    YCrosstab,
    YGenderItem,
    YGenerationItem,
    YHourlyTrafficItem,
    YKeywordItem,
    YKeywords,
    YOverview,
    YPeakSlot,
    YPurposeItem,
    YRadarBenchmarkItem,
    YRadarStores,
    YRatingDistribution,
    YRatingPoint,
    YRatingSeries,
    YRatingSummary,
    YRatingSummaryItem,
    YReportViewOut,
    YRetention,
    YRiskScore,
    YSideEffectItem,
    YTradeoffItem,
    YVisitStats,
)
from app.utils.json_parser import JSONParseError, safe_json_loads

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

MARKET_SHARE_DECIMALS = 1   # marketShare 소수점 자리
RETENTION_DECIMALS = 1      # retentionRate / newRatio: 0~100 % 소수점 자리

_DEFAULT_RISK_LEVEL = "알 수 없음"

# sim_to_y 슬롯 레이블 (int hour → 표시 레이블)
# TIME_SLOT_MAP = {"아침": 7, "점심": 12, "저녁": 18, "야식": 22}
_SIM_SLOT_LABELS: Dict[int, str] = {
    7: "아침(07)",
    12: "점심(12)",
    18: "저녁(18)",
    22: "야식(22)",
}

# verdict → sideEffect type 매핑
_VERDICT_TYPE: Dict[str, str] = {
    "역효과": "warning",
    "관찰 필요": "watch",
    "순기능": "positive",
}


# ── 범위/타입 변환 헬퍼 ──────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_positive_int(v: Any) -> int:
    return max(0, _safe_int(v))


def _safe_pct(v: Any) -> float:
    """0~100 범위 퍼센트 값으로 clamp."""
    return round(_clamp(_safe_float(v), 0.0, 100.0), RETENTION_DECIMALS)


# ── output_json 파싱 ─────────────────────────────────────────────────────────

def _parse_output_json(report: YReport) -> Optional[Dict[str, Any]]:
    """output_json → dict 변환. 파싱 실패 시 None."""
    raw = report.output_json
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return safe_json_loads(raw)
        except JSONParseError:
            return None
    return None


def _get_sections(output: Dict[str, Any]) -> Dict[str, Any]:
    """output_json에서 metrics_summary 섹션 dict를 반환한다.

    실제 sim_to_y 출력: output["structured"] = metrics_summary (섹션 키 포함)
    레거시/테스트 데이터: output["structured"] = 평탄 dict

    Returns:
        섹션 dict (top-level 키: "overview", "rating", "traffic", ...)
        섹션 포맷이 아니면 빈 dict
    """
    structured = output.get("structured") or {}
    if not isinstance(structured, dict):
        return {}

    # 실제 sim_to_y 포맷 감지: 섹션 키 중 하나라도 있으면 "sectioned" 모드
    _SECTION_KEYS = {"overview", "rating", "traffic", "generation", "purpose",
                     "retention", "radar", "agent_type", "gender", "crosstab",
                     "safety", "tradeoff"}
    is_sectioned = bool(_SECTION_KEYS & set(structured.keys()))

    if is_sectioned:
        logger.info(
            "y_report_view: sectioned metrics_summary format (sim_to_y output)",
            extra={"extra_fields": {"sections": sorted(_SECTION_KEYS & set(structured.keys()))}},
        )
        return structured

    # 레거시/테스트 포맷: _before/_after 접미사 패턴
    has_before_after = any(k.endswith("_before") or k.endswith("_after") for k in structured)
    if has_before_after:
        logger.info("y_report_view: flat before_after format (legacy/test data)")
        # 평탄 포맷은 호환 래퍼를 만들어 반환
        return _wrap_flat_as_sections(structured)

    logger.warning(
        "y_report_view: unknown structured format, using empty sections",
        extra={"extra_fields": {"keys": list(structured.keys())[:8]}},
    )
    return {}


def _wrap_flat_as_sections(flat: Dict[str, Any]) -> Dict[str, Any]:
    """레거시 평탄 포맷을 섹션 포맷으로 래핑 (하위 호환)."""
    return {
        "overview": {
            "visits_before":       flat.get("total_visits_before", flat.get("visits_before", 0)),
            "visits_after":        flat.get("total_visits_after",  flat.get("visits_after",  0)),
            "visit_change_pct":    flat.get("visit_change_pct", 0),
            "market_share_before": flat.get("market_share_before", 0.0),
            "market_share_after":  flat.get("market_share_after",  0.0),
        },
        "rating": {
            "avg_rating_before":         flat.get("avg_rating_before", 0.0),
            "avg_rating_after":          flat.get("avg_rating_after",  0.0),
            "satisfaction_rate_before":  flat.get("satisfaction_before", 0.0),
            "satisfaction_rate_after":   flat.get("satisfaction_after",  0.0),
        },
        "retention": {
            "visitors_before":  flat.get("total_visits_before", 0),
            "visitors_after":   flat.get("total_visits_after",  0),
            "retained":         0,
            "new_users":        0,
            "churned":          0,
            "retention_rate":   flat.get("retention_rate", 0.0) * 100 if flat.get("retention_rate", 1.0) <= 1.0
                                else flat.get("retention_rate", 0.0),
            "new_user_rate":    0.0,
        },
    }


# ── 섹션별 빌더 ──────────────────────────────────────────────────────────────

def _build_overview(sections: Dict[str, Any]) -> YOverview:
    """overview: sim1(before)/sim2(after) 방문수 + 시장점유율.

    섹션 키: "overview"
      - visits_before, visits_after, market_share_before, market_share_after
    """
    ov = sections.get("overview") or {}
    v1 = _safe_positive_int(ov.get("visits_before"))
    v2 = _safe_positive_int(ov.get("visits_after"))
    m1 = round(_clamp(_safe_float(ov.get("market_share_before", 0.0)), 0.0, 100.0), MARKET_SHARE_DECIMALS)
    m2 = round(_clamp(_safe_float(ov.get("market_share_after",  0.0)), 0.0, 100.0), MARKET_SHARE_DECIMALS)
    return YOverview(
        sim1=YVisitStats(totalVisits=v1, marketShare=m1),
        sim2=YVisitStats(totalVisits=v2, marketShare=m2),
    )


def _build_keywords(sections: Dict[str, Any]) -> YKeywords:
    """keywords: sim_to_y는 현재 워드클라우드 이미지만 생성, JSON에 저장 안 함.

    TODO: sim_to_y.py가 keywords 데이터를 metrics_summary에 추가하면 여기서 파싱.
    현재는 항상 빈 리스트 반환.
    """
    # 미래 대비: "keywords" 섹션이 추가될 경우
    kw = sections.get("keywords")
    if isinstance(kw, dict):
        def _parse(raw: Any) -> List[YKeywordItem]:
            if not isinstance(raw, list):
                return []
            result = []
            for item in raw:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("keyword") or "").strip()
                    weight = _safe_float(item.get("weight") or item.get("count") or 0)
                    if text:
                        result.append(YKeywordItem(text=text, weight=weight))
            return result
        return YKeywords(sim1=_parse(kw.get("sim1")), sim2=_parse(kw.get("sim2")))
    return YKeywords(sim1=[], sim2=[])


def _build_rating_distribution(sections: Dict[str, Any]) -> Optional[YRatingDistribution]:
    """ratingDistribution: 평점 분포.

    섹션 키: "rating"
      - avg_rating_before, avg_rating_after → taste 항목 단일 포인트
    """
    rt = sections.get("rating") or {}
    if not rt:
        return None

    avg_b = rt.get("avg_rating_before")
    avg_a = rt.get("avg_rating_after")
    if avg_b is None and avg_a is None:
        return None

    def _point(score: float) -> List[YRatingPoint]:
        return [YRatingPoint(score=round(_clamp(score, 0.0, 5.0), 2), density=1.0)]

    taste = YRatingSeries(
        sim1=_point(_safe_float(avg_b)),
        sim2=_point(_safe_float(avg_a if avg_a is not None else avg_b)),
    )
    return YRatingDistribution(taste=taste)


def _build_rating_summary(sections: Dict[str, Any]) -> Optional[YRatingSummary]:
    """ratingSummary: 평균 평점 + 만족도.

    섹션 키: "rating"
      - avg_rating_before/after, satisfaction_rate_before/after
    """
    rt = sections.get("rating") or {}
    avg1 = rt.get("avg_rating_before")
    avg2 = rt.get("avg_rating_after")
    if avg1 is None and avg2 is None:
        return None

    # satisfaction_rate: sim_to_y 저장 키 (0~100 %)
    sat1 = _safe_float(rt.get("satisfaction_rate_before", 0.0))
    sat2 = _safe_float(rt.get("satisfaction_rate_after",  0.0))
    return YRatingSummary(
        sim1=YRatingSummaryItem(avg=_safe_float(avg1), satisfaction=sat1),
        sim2=YRatingSummaryItem(avg=_safe_float(avg2), satisfaction=sat2),
    )


def _build_hourly_traffic(sections: Dict[str, Any]) -> List[YHourlyTrafficItem]:
    """hourlyTraffic: [{slot, sim1, sim2}].

    섹션 키: "traffic"
      - traffic_before: {int_hour: count} (JSON 직렬화 후 str 키 가능)
      - traffic_after:  {int_hour: count}
      - 슬롯 레이블: _SIM_SLOT_LABELS (7→"아침(07)", 12→"점심(12)", ...)
    """
    tr = sections.get("traffic") or {}
    if not tr:
        return []

    tb = tr.get("traffic_before") or {}
    ta = tr.get("traffic_after") or {}

    # JSON 직렬화 후 int 키가 str이 되므로 양쪽 지원
    def _to_int_key(d: dict) -> Dict[int, int]:
        result = {}
        for k, v in d.items():
            try:
                result[int(k)] = _safe_int(v)
            except (ValueError, TypeError):
                pass
        return result

    tb_int = _to_int_key(tb)
    ta_int = _to_int_key(ta)
    all_hours = sorted(set(tb_int) | set(ta_int))

    result = []
    for hour in all_hours:
        slot = _SIM_SLOT_LABELS.get(hour, f"{hour:02d}시")
        result.append(YHourlyTrafficItem(
            slot=slot,
            sim1=_safe_positive_int(tb_int.get(hour, 0)),
            sim2=_safe_positive_int(ta_int.get(hour, 0)),
        ))
    return result


def _build_peak_slot(sections: Dict[str, Any]) -> Optional[YPeakSlot]:
    """peakSlot: {sim1, sim2} 피크 시간대.

    섹션 키: "traffic"
      - peak_slot_before, peak_slot_after (이미 레이블 문자열: "아침(07)" 등)
    """
    tr = sections.get("traffic") or {}
    s1 = str(tr.get("peak_slot_before") or "")
    s2 = str(tr.get("peak_slot_after") or "")
    if s1 or s2:
        return YPeakSlot(sim1=s1, sim2=s2)
    return None


def _build_retention(sections: Dict[str, Any]) -> YRetention:
    """retention: 재방문율 지표. 항상 구조 반환.

    섹션 키: "retention"
      - visitors_before / visitors_after → sim1Agents / sim2Agents
      - retained, new_users, churned
      - retention_rate: 0~100 (%)
      - new_user_rate:  0~100 (%)
    """
    ret = sections.get("retention") or {}
    v1       = _safe_positive_int(ret.get("visitors_before"))
    v2       = _safe_positive_int(ret.get("visitors_after"))
    retained = _safe_positive_int(ret.get("retained"))
    new_users = _safe_positive_int(ret.get("new_users"))
    churned  = _safe_positive_int(ret.get("churned"))
    ret_rate = _safe_pct(ret.get("retention_rate", 0.0))
    new_rate = _safe_pct(ret.get("new_user_rate", 0.0))

    return YRetention(
        sim1Agents=v1,
        sim2Agents=v2,
        retained=retained,
        newUsers=new_users,
        churned=churned,
        retentionRate=ret_rate,
        newRatio=new_rate,
    )


def _build_generation(sections: Dict[str, Any]) -> List[YGenerationItem]:
    """generation: [{gen, sim1, sim2}].

    섹션 키: "generation"
      - before: {gen_label: float(%)}
      - after:  {gen_label: float(%)}
    """
    gen = sections.get("generation") or {}
    before = gen.get("before") or {}
    after  = gen.get("after")  or {}
    if not before and not after:
        return []

    all_gens = list(before.keys()) if before else list(after.keys())
    result = []
    for g in all_gens:
        b_val = _safe_float(before.get(g, 0.0))
        a_val = _safe_float(after.get(g, 0.0))
        # 퍼센트 값을 int로 반올림 (DTO: int)
        result.append(YGenerationItem(
            gen=str(g),
            sim1=int(round(b_val)),
            sim2=int(round(a_val)),
        ))
    return result


def _build_purpose(sections: Dict[str, Any]) -> List[YPurposeItem]:
    """purpose: [{type, sim1Pct, sim2Pct, sim1Sat, sim2Sat}].

    섹션 키: "purpose"
      - purpose_ratio_before / purpose_ratio_after   : {keyword: float(%)}
      - purpose_satisfaction_before / _after : {keyword: float(0~5)}
    """
    pur = sections.get("purpose") or {}
    rb = pur.get("purpose_ratio_before") or {}
    ra = pur.get("purpose_ratio_after")  or {}
    sb = pur.get("purpose_satisfaction_before") or {}
    sa = pur.get("purpose_satisfaction_after")  or {}
    if not rb and not ra:
        return []

    all_types = list(rb.keys()) if rb else list(ra.keys())
    result = []
    for t in all_types:
        result.append(YPurposeItem(
            type=str(t),
            sim1Pct=round(_safe_float(rb.get(t, 0.0)), 1),
            sim2Pct=round(_safe_float(ra.get(t, 0.0)), 1),
            sim1Sat=round(_safe_float(sb.get(t, 0.0)), 2),
            sim2Sat=round(_safe_float(sa.get(t, 0.0)), 2),
        ))
    return result


def _build_radar(sections: Dict[str, Any]) -> List[YRadarBenchmarkItem]:
    """radar: [{metric, unit, targetBefore, targetAfter, comp1-3}].

    섹션 키: "radar" (구조는 analysis_7_radar_chart()에 따라 가변)
    현재는 데이터 없으면 빈 리스트.
    """
    radar = sections.get("radar") or {}
    if not isinstance(radar, dict) or not radar:
        return []

    # top_stores + target_metrics_before/after 구조
    top_stores = radar.get("top_stores") or []
    tm_before  = radar.get("target_metrics_before") or {}
    tm_after   = radar.get("target_metrics_after")  or {}

    if not top_stores and not tm_before:
        return []

    # 레이더 축 후보: target_metrics의 키
    metrics = list(tm_before.keys() or tm_after.keys())
    if not metrics:
        return []

    # 경쟁사 지표
    comp_names = [s.get("store_name", "") if isinstance(s, dict) else str(s) for s in top_stores[:3]]
    comp_metrics_list = [
        (s.get("metrics") or {}) if isinstance(s, dict) else {}
        for s in top_stores[:3]
    ]

    result = []
    for m in metrics:
        comp_vals = [_safe_float(cm.get(m, 0.0)) for cm in comp_metrics_list]
        while len(comp_vals) < 3:
            comp_vals.append(0.0)
        result.append(YRadarBenchmarkItem(
            metric=str(m),
            unit="",
            targetBefore=_safe_float(tm_before.get(m, 0.0)),
            targetAfter=_safe_float(tm_after.get(m, 0.0)),
            comp1=comp_vals[0],
            comp2=comp_vals[1],
            comp3=comp_vals[2],
        ))
    return result


def _build_radar_stores(sections: Dict[str, Any]) -> Optional[YRadarStores]:
    """radarStores: {comp1, comp2, comp3} 경쟁사명."""
    radar = sections.get("radar") or {}
    top_stores = radar.get("top_stores") or []
    names = [
        (s.get("store_name", "") if isinstance(s, dict) else str(s))
        for s in top_stores[:3]
    ]
    while len(names) < 3:
        names.append("")
    if any(names):
        return YRadarStores(comp1=names[0], comp2=names[1], comp3=names[2])
    return None


def _build_agent_type(sections: Dict[str, Any]) -> List[YAgentTypeItem]:
    """agentType: [{type, sim1, sim2}].

    섹션 키: "agent_type"
      - before: {type: float(%)}
      - after:  {type: float(%)}
      - types:  [str]
    """
    at = sections.get("agent_type") or {}
    before = at.get("before") or {}
    after  = at.get("after")  or {}
    types  = at.get("types")  or list(before.keys()) or list(after.keys())
    if not types:
        return []

    result = []
    for t in types:
        result.append(YAgentTypeItem(
            type=str(t),
            sim1=int(round(_safe_float(before.get(t, 0.0)))),
            sim2=int(round(_safe_float(after.get(t, 0.0)))),
        ))
    return result


def _build_gender(sections: Dict[str, Any]) -> List[YGenderItem]:
    """gender: [{label, sim1, sim2}].

    섹션 키: "gender"
      - before:  {label: float(%)}
      - after:   {label: float(%)}
      - genders: [str]
    """
    gd = sections.get("gender") or {}
    before  = gd.get("before")  or {}
    after   = gd.get("after")   or {}
    genders = gd.get("genders") or list(before.keys()) or list(after.keys())
    if not genders:
        return []

    result = []
    for g in genders:
        result.append(YGenderItem(
            label=str(g),
            sim1=int(round(_safe_float(before.get(g, 0.0)))),
            sim2=int(round(_safe_float(after.get(g, 0.0)))),
        ))
    return result


def _build_crosstab(sections: Dict[str, Any]) -> Optional[YCrosstab]:
    """crosstab: {generations, purposes, sim2}.

    섹션 키: "crosstab"
      - generations: [str]
      - purposes:    [str]
      - sim2:        [[float, ...], ...]  (세대×목적 크로스탭 after 기준)
    """
    ct = sections.get("crosstab") or {}
    if not isinstance(ct, dict) or not ct:
        return None
    return YCrosstab(
        generations=ct.get("generations") or [],
        purposes=ct.get("purposes") or [],
        sim2=ct.get("sim2") or [],
    )


def _build_side_effects(sections: Dict[str, Any]) -> List[YSideEffectItem]:
    """sideEffects: [{type, metric, change, unit, detail}].

    섹션 키: "safety"
      - findings: [{metric, before, after, delta, unit, verdict, message}]
      - verdict → type: 역효과→warning, 관찰 필요→watch
      - 순기능은 제외 (side effect 아님)
    """
    safety = sections.get("safety") or {}
    findings = safety.get("findings") or []
    if not findings:
        return []

    result = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        verdict = str(f.get("verdict") or "")
        if verdict == "순기능":
            continue  # 순기능은 side effect 아님
        effect_type = _VERDICT_TYPE.get(verdict, "watch")
        metric = str(f.get("metric") or "")
        if not metric:
            continue
        result.append(YSideEffectItem(
            type=effect_type,
            metric=metric,
            change=_safe_float(f.get("delta") or f.get("change") or 0),
            unit=str(f.get("unit") or ""),
            detail=str(f.get("message") or f.get("detail") or ""),
        ))
    return result


def _build_tradeoffs(sections: Dict[str, Any]) -> List[YTradeoffItem]:
    """tradeoffs: [{gain, gainVal, loss, lossVal}].

    섹션 키: "tradeoff"
      - gains:  [{metric: str, delta: str}]
      - losses: [{metric: str, delta: str}]
    사이즈 다를 경우 짧은 쪽을 빈 문자열로 패딩.
    """
    td = sections.get("tradeoff") or {}
    gains  = td.get("gains")  or []
    losses = td.get("losses") or []
    if not gains and not losses:
        return []

    max_len = max(len(gains), len(losses))
    result = []
    for i in range(max_len):
        g = gains[i]  if i < len(gains)  else {}
        l = losses[i] if i < len(losses) else {}
        result.append(YTradeoffItem(
            gain=str(g.get("metric") or "") if isinstance(g, dict) else "",
            gainVal=str(g.get("delta") or "") if isinstance(g, dict) else "",
            loss=str(l.get("metric") or "") if isinstance(l, dict) else "",
            lossVal=str(l.get("delta") or "") if isinstance(l, dict) else "",
        ))
    return result


def _build_risk_score(sections: Dict[str, Any]) -> YRiskScore:
    """riskScore: 항상 구조 반환.

    섹션 키: "safety"
      - risk_score:   int (0~100)
      - 순기능_count, 관찰_count, 역효과_count
      - findings:     총 지표 수 계산용
    """
    safety = sections.get("safety") or {}
    score    = int(_clamp(_safe_float(safety.get("risk_score", 0)), 0, 100))
    positive = _safe_int(safety.get("순기능_count"))
    watch    = _safe_int(safety.get("관찰_count"))
    negative = _safe_int(safety.get("역효과_count"))
    total    = positive + watch + negative

    # level 결정
    if score == 0 and not safety:
        level = _DEFAULT_RISK_LEVEL
    elif score >= 60:
        level = "높은 위험"
    elif score >= 30:
        level = "중간 위험"
    else:
        level = "낮은 위험"

    return YRiskScore(
        score=score,
        level=level,
        positive=positive,
        watch=watch,
        negative=negative,
        totalMetrics=total,
    )


def _build_llm_summary(report: YReport, output: Optional[Dict[str, Any]]) -> Optional[str]:
    """llmSummary: markdown → output.summary → ""."""
    if report.markdown:
        return report.markdown
    if isinstance(output, dict):
        summary = (
            output.get("summary")
            or output.get("llm_summary")
            or output.get("markdown")
        )
        if summary:
            return str(summary)
    return ""


# ── 기본값 빌더 ───────────────────────────────────────────────────────────────

def _default_overview() -> YOverview:
    return YOverview(
        sim1=YVisitStats(totalVisits=0, marketShare=0.0),
        sim2=YVisitStats(totalVisits=0, marketShare=0.0),
    )


def _default_keywords() -> YKeywords:
    return YKeywords(sim1=[], sim2=[])


def _default_retention() -> YRetention:
    return YRetention(
        sim1Agents=0, sim2Agents=0,
        retained=0, newUsers=0, churned=0,
        retentionRate=0.0, newRatio=0.0,
    )


def _default_risk_score() -> YRiskScore:
    return YRiskScore(
        score=0, level=_DEFAULT_RISK_LEVEL,
        positive=0, watch=0, negative=0, totalMetrics=0,
    )


# ── 메인 서비스 함수 ──────────────────────────────────────────────────────────

async def build_y_report_view(
    session: AsyncSession,
    report_id: int,
) -> Optional[YReportViewOut]:
    """GET /y-reports/{id}/view 서비스 함수.

    Returns:
        YReportViewOut  — 성공 (200)
        None            — 해당 y_report 없음 (router → 404)

    Raises:
        ValueError("[YREPORT_PARSE_ERROR]") — 파싱 실패 + markdown 없음 (router → 500)
    """
    repo = YReportRepo(session)
    report = await repo.get_by_id(report_id)
    if report is None:
        return None

    output = _parse_output_json(report)

    if output is None and not report.markdown:
        raise ValueError(
            f"[YREPORT_PARSE_ERROR] y_report id={report_id}: "
            "output_json 파싱 실패 및 markdown 없음 — View DTO 생성 불가"
        )

    sections: Dict[str, Any] = _get_sections(output) if output is not None else {}

    data: Dict[str, Any] = {
        "id":               report.id,
        "store_source_id":  report.store_source_id,
        "simulation_id":    report.simulation_id,
        "x_report_id":      report.x_report_id,
        "status":           report.status,
        "overview":         _build_overview(sections) if sections else _default_overview(),
        "keywords":         _build_keywords(sections),
        "rating_distribution": _build_rating_distribution(sections),
        "rating_summary":   _build_rating_summary(sections),
        "hourly_traffic":   _build_hourly_traffic(sections),
        "peak_slot":        _build_peak_slot(sections),
        "generation":       _build_generation(sections),
        "purpose":          _build_purpose(sections),
        "retention":        _build_retention(sections) if sections else _default_retention(),
        "radar":            _build_radar(sections),
        "radar_stores":     _build_radar_stores(sections),
        "agent_type":       _build_agent_type(sections),
        "gender":           _build_gender(sections),
        "crosstab":         _build_crosstab(sections),
        "side_effects":     _build_side_effects(sections),
        "tradeoffs":        _build_tradeoffs(sections),
        "risk_score":       _build_risk_score(sections) if sections else _default_risk_score(),
        "llm_summary":      _build_llm_summary(report, output),
    }

    return YReportViewOut.model_validate(data)
