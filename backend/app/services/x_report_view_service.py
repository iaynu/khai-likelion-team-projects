"""
X-Report View Service — GET /x-reports/{id}/view

데이터 파이프라인:
  1) XReportRepo.get_by_id()          → XReport ORM
  2) StrategyRepo.list_by_report()    → [Strategy ORM]
  3) _build_solutions()               → solutions (3-source priority chain)
  4) _build_radar_data()              → radarData (5개 축)
  5) _build_keywords()                → keywords
  6) XReportViewOut 조립 및 반환

solutions 우선순위:
  P1) config_json['categories']       → {category, title, desc, execution, effect}
  P2) config_json['strategies']       → {solution_group, title, description, impact_mapping}
  P3) DB strategies 테이블 rows       → Strategy ORM (동일 매핑)
"""
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import Strategy
from app.models.x_report import XReport
from app.repositories.store_repo import StoreRepo
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.x_report_repo import XReportRepo
from app.schemas.x_report import KeywordItem, RadarDataItem, SolutionItem, XReportViewOut

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

_RADAR_SUBJECTS: List[str] = ["맛", "서비스", "분위기", "가격", "위생"]

# solution_group (1~3) → 카테고리 레이블
_GROUP_LABELS = {
    1: "운영 효율",
    2: "마케팅",
    3: "고객 서비스",
}

_MAX_SOLUTIONS = 9


# ── 내부 헬퍼: Solutions ──────────────────────────────────────────────────────

def _impact_to_execution(impact: Optional[dict]) -> str:
    """impact_mapping dict 에서 실행 힌트 문자열 추출."""
    if not impact:
        return ""
    for key in ("execution", "short_term", "action"):
        if impact.get(key):
            return str(impact[key])
    # 첫 번째 값 fallback
    for v in impact.values():
        if v and isinstance(v, str):
            return v
    return ""


def _impact_to_effect(impact: Optional[dict]) -> str:
    """impact_mapping dict 에서 기대 효과 문자열 추출."""
    if not impact:
        return ""
    for key in ("effect", "long_term", "outcome"):
        if impact.get(key):
            return str(impact[key])
    return ""


def _solutions_from_categories(categories: List[dict]) -> List[SolutionItem]:
    """P1: config_json['categories'] 포맷 — 필드가 정확히 일치."""
    result = []
    for c in categories[:_MAX_SOLUTIONS]:
        if not isinstance(c, dict):
            continue
        result.append(
            SolutionItem(
                category=str(c.get("category") or ""),
                title=str(c.get("title") or ""),
                desc=str(c.get("desc") or ""),
                execution=str(c.get("execution") or ""),
                effect=str(c.get("effect") or ""),
            )
        )
    return result


def _solutions_from_strategies_list(strategies: List[dict]) -> List[SolutionItem]:
    """P2: config_json['strategies'] 포맷 — {solution_group, title, description, impact_mapping}.

    그룹별 최대 3개씩 균등 배분 (총 9개).
    flat slice 대신 group별로 수집하여 4-4-1 쏠림 방지.
    """
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for s in strategies:
        if not isinstance(s, dict):
            continue
        group = s.get("solution_group") or 0
        groups[group].append(s)

    result = []
    for group in sorted(groups.keys()):
        for s in groups[group][:3]:  # 그룹당 최대 3개
            impact = s.get("impact_mapping") if isinstance(s.get("impact_mapping"), dict) else {}
            desc = str(s.get("description") or "")
            result.append(
                SolutionItem(
                    category=_GROUP_LABELS.get(group, f"그룹{group}"),
                    title=str(s.get("title") or ""),
                    desc=desc,
                    execution=_impact_to_execution(impact) or desc,
                    effect=_impact_to_effect(impact),
                )
            )
    return result[:_MAX_SOLUTIONS]


def _solutions_from_db_strategies(db_strategies: List[Strategy]) -> List[SolutionItem]:
    """P3: DB strategies 테이블 — Strategy ORM rows."""
    sorted_strats = sorted(
        db_strategies,
        key=lambda s: (s.solution_group or 0, s.action_idx or 0),
    )
    result = []
    for s in sorted_strats[:_MAX_SOLUTIONS]:
        group = s.solution_group or 0
        impact = s.impact_mapping if isinstance(s.impact_mapping, dict) else {}
        desc = str(s.description or "")
        result.append(
            SolutionItem(
                category=_GROUP_LABELS.get(group, f"그룹{group}"),
                title=str(s.title or ""),
                desc=desc,
                execution=_impact_to_execution(impact) or desc,
                effect=_impact_to_effect(impact),
            )
        )
    return result


def _build_solutions(report: XReport, db_strategies: List[Strategy]) -> List[SolutionItem]:
    """3-source 우선순위 체인으로 SolutionItem 목록을 구성한다."""
    config = report.config_json or {}

    # P1: categories 포맷
    categories = config.get("categories")
    if isinstance(categories, list) and categories:
        solutions = _solutions_from_categories(categories)
        if solutions:
            logger.debug("x_report_view: solutions from categories (P1)", extra={"extra_fields": {"report_id": report.id}})
            return solutions

    # P2: strategies 포맷 (config_json)
    strategies_cfg = config.get("strategies")
    if isinstance(strategies_cfg, list) and strategies_cfg:
        solutions = _solutions_from_strategies_list(strategies_cfg)
        if solutions:
            logger.debug("x_report_view: solutions from config_json.strategies (P2)", extra={"extra_fields": {"report_id": report.id}})
            return solutions

    # P3: DB Strategy 테이블
    if db_strategies:
        logger.debug("x_report_view: solutions from DB strategies (P3)", extra={"extra_fields": {"report_id": report.id}})
        return _solutions_from_db_strategies(db_strategies)

    logger.warning("x_report_view: no solutions source found", extra={"extra_fields": {"report_id": report.id}})
    return []


# ── 내부 헬퍼: RadarData ──────────────────────────────────────────────────────

def _build_radar_data(report: XReport) -> List[RadarDataItem]:
    """config_json['radar_data'] 파싱; 없으면 5-subject 제로 플레이스홀더."""
    config = report.config_json or {}
    raw = config.get("radar_data")

    if isinstance(raw, list) and raw:
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                result.append(
                    RadarDataItem(
                        subject=str(item.get("subject") or ""),
                        A=int(item.get("A") or 0),
                        fullMark=int(item.get("fullMark") or 100),
                        reason=item.get("reason") or None,
                    )
                )
            except (TypeError, ValueError):
                pass
        if result:
            return result

    # 플레이스홀더: 모든 축 A=0
    return [
        RadarDataItem(subject=subject, A=0, fullMark=100)
        for subject in _RADAR_SUBJECTS
    ]


# ── 내부 헬퍼: Keywords ───────────────────────────────────────────────────────

_VALID_SENTIMENTS = {"neutral", "positive", "negative"}


def _build_keywords(report: XReport) -> List[KeywordItem]:
    """config_json['keywords'] 파싱; 없으면 []."""
    config = report.config_json or {}
    raw = config.get("keywords")

    if not isinstance(raw, list):
        return []

    result = []
    for kw in raw:
        if isinstance(kw, dict):
            text = str(kw.get("text") or kw.get("keyword") or "").strip()
            sentiment = str(kw.get("sentiment") or "neutral")
            if sentiment not in _VALID_SENTIMENTS:
                sentiment = "neutral"
            if text:
                result.append(KeywordItem(text=text, sentiment=sentiment))
        elif isinstance(kw, str) and kw.strip():
            result.append(KeywordItem(text=kw.strip(), sentiment="neutral"))

    return result


# ── 메인 서비스 함수 ──────────────────────────────────────────────────────────

async def build_x_report_view(
    session: AsyncSession,
    report_id: int,
) -> Optional[XReportViewOut]:
    """GET /x-reports/{id}/view 서비스 함수.

    Returns:
        XReportViewOut  — 성공
        None            — 해당 x_report 없음 (router 에서 404 처리)
    """
    repo = XReportRepo(session)
    report = await repo.get_by_id(report_id)
    if report is None:
        return None

    strat_repo = StrategyRepo(session)
    db_strategies = await strat_repo.list_by_report(report_id)

    # store_name: store_id 로 Store 조회 (없으면 None)
    store = await StoreRepo(session).get_by_id(report.store_id)
    store_name = store.store_name if store else None

    solutions = _build_solutions(report, db_strategies)
    radar_data = _build_radar_data(report)
    keywords = _build_keywords(report)

    # ── 등급 / 상위% ─────────────────────────────────────────────────────────
    grade: Optional[str] = None
    rank_percent: Optional[int] = None
    if radar_data:
        from app.services.store_grade_service import get_grade_and_rank
        avg_score = sum(item.A for item in radar_data) / len(radar_data)
        grade, rank_percent = get_grade_and_rank(avg_score)

    return XReportViewOut(
        id=report.id,
        store_id=report.store_id,
        name=store_name,
        status=report.status,
        grade=grade,
        rank_percent=rank_percent,
        radar_data=radar_data,
        keywords=keywords,
        solutions=solutions,
        full_report=report.content_text,
    )
