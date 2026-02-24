"""
Store Service — GET /stores View 레이어

데이터 파이프라인:
  1) StoreRepo.search()       → [Store ORM] (q 필터, limit/offset)
  2) StoreRepo.count_filtered() → total (q 필터만 적용한 전체 개수)
  3) _fetch_docs_map()        → {store_id: payload dict} (batch, 최신 문서 1개/매장)
  4) _enrich()                → StoreListItemOut 변환 (lat/lng/sentiment 파싱)
  5) lat/lng null 비율 체크    → 80%+ null 이면 fallback (전체 반환 + 경고 로그)
"""
import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store_document import StoreDocument
from app.repositories.store_repo import StoreRepo
from app.schemas.store import StoreListItemOut, StoreListViewOut

logger = logging.getLogger(__name__)

_LAT_KEYS = ("lat", "latitude")
_LNG_KEYS = ("lng", "longitude")

# ── CSV 주소 캐시 ────────────────────────────────────────────────────────────

_csv_address_map: Optional[Dict[str, str]] = None  # {store_source_id: 주소}


def _load_csv_address_map() -> Dict[str, str]:
    """data/raw/stores.csv 를 읽어 {ID → 주소} 매핑 반환 (모듈 레벨 캐시)."""
    global _csv_address_map
    if _csv_address_map is not None:
        return _csv_address_map

    result: Dict[str, str] = {}
    try:
        csv_path = Path(__file__).parent.parent.parent / "data" / "raw" / "stores.csv"
        if csv_path.exists():
            with open(csv_path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    store_id = str(row.get("ID") or "").strip()
                    address = str(row.get("주소") or "").strip()
                    if store_id and address:
                        result[store_id] = address
            logger.info("stores_csv_address_loaded: count=%d", len(result))
        else:
            logger.warning("stores_csv_not_found: %s", csv_path)
    except Exception as exc:
        logger.warning("stores_csv_load_failed: %s", exc)

    _csv_address_map = result
    return _csv_address_map


# ── 좌표 파싱 헬퍼 ──────────────────────────────────────────────────────────

def _extract_coords(payload: Optional[dict]) -> Tuple[Optional[float], Optional[float]]:
    """payload dict에서 (lat, lng)를 추출한다.

    탐색 순서:
      1) payload 직하위 lat/latitude, lng/longitude 키
      2) payload.analysis_reports[*]의 동일 키
    """
    if not payload:
        return None, None

    def _pick(d: dict) -> Tuple[Optional[float], Optional[float]]:
        lat = next((d[k] for k in _LAT_KEYS if k in d), None)
        lng = next((d[k] for k in _LNG_KEYS if k in d), None)
        if lat is not None and lng is not None:
            try:
                return float(lat), float(lng)
            except (TypeError, ValueError):
                pass
        return None, None

    lat, lng = _pick(payload)
    if lat is not None:
        return lat, lng

    for report in payload.get("analysis_reports", []):
        if isinstance(report, dict):
            lat, lng = _pick(report)
            if lat is not None:
                return lat, lng

    return None, None


def _extract_sentiment(payload: Optional[dict]) -> Optional[float]:
    """payload.analysis_reports[0].review_metrics.overall_sentiment.score 파싱."""
    if not payload:
        return None
    for report in payload.get("analysis_reports", []):
        if not isinstance(report, dict):
            continue
        metrics = report.get("review_metrics") or {}
        score = metrics.get("overall_sentiment", {}).get("score")
        if score is not None:
            try:
                return float(score)
            except (TypeError, ValueError):
                pass
    return None


def _extract_address(
    payload: Optional[dict],
    area_code: Optional[str],
    store_source_id: Optional[str] = None,
) -> Optional[str]:
    """address 파싱.

    우선순위:
      1) stores.csv ID → 주소 (가장 정확한 실제 주소)
      2) payload.analysis_reports[*].address 등
      3) area_code fallback
    """
    # P1: CSV 실제 주소
    if store_source_id:
        csv_addr = _load_csv_address_map().get(str(store_source_id))
        if csv_addr:
            return csv_addr

    # P2: payload 파싱
    if payload:
        for report in payload.get("analysis_reports", []):
            if isinstance(report, dict):
                for key in ("address", "store_address", "location"):
                    if report.get(key):
                        return str(report[key])

    # P3: area_code fallback
    return area_code or None


# ── 배치 문서 조회 ──────────────────────────────────────────────────────────

async def _fetch_docs_map(session: AsyncSession, store_ids: List[str]) -> Dict[str, dict]:
    """store_id 목록에 대해 최신 StoreDocument(payload)를 배치로 가져온다.

    각 store_id마다 가장 최근 문서 1개만 반환 (id DESC 기준).
    """
    if not store_ids:
        return {}

    # 최신 doc id 서브쿼리
    subq = (
        select(
            StoreDocument.store_id,
            func.max(StoreDocument.id).label("max_id"),
        )
        .where(StoreDocument.store_id.in_(store_ids))
        .group_by(StoreDocument.store_id)
        .subquery()
    )
    result = await session.execute(
        select(StoreDocument).join(subq, StoreDocument.id == subq.c.max_id)
    )
    docs = result.scalars().all()
    return {d.store_id: (d.payload if isinstance(d.payload, dict) else {}) for d in docs}


# ── 메인 서비스 함수 ────────────────────────────────────────────────────────

async def list_stores_view(
    session: AsyncSession,
    q: Optional[str],
    limit: int,
    offset: int,
) -> StoreListViewOut:
    """GET /stores 서비스 레이어.

    Returns:
        StoreListViewOut — items / total / limit / offset
    """
    repo = StoreRepo(session)

    stores = await repo.search(q=q, limit=limit, offset=offset)
    total = await repo.count_filtered(q=q)

    if not stores:
        return StoreListViewOut(items=[], total=total, limit=limit, offset=offset)

    store_ids = [s.id for s in stores]
    docs_map = await _fetch_docs_map(session, store_ids)

    # ── 개별 매장 enrich ────────────────────────────────────────────────────
    enriched: List[StoreListItemOut] = []
    null_coord_count = 0

    for store in stores:
        payload = docs_map.get(store.id)
        lat, lng = _extract_coords(payload)
        if lat is None or lng is None:
            null_coord_count += 1

        enriched.append(
            StoreListItemOut(
                id=store.store_source_id,
                name=store.store_name,
                address=_extract_address(payload, store.area_code, store.store_source_id),
                lat=lat,
                lng=lng,
                sentiment=_extract_sentiment(payload),
                revenue=None,    # TODO: revenue_analysis 파싱 (추후 구조 확정 시)
                grade=None,      # TODO: X-Report 구조화 데이터 필요
                rankPercent=None,  # TODO: X-Report 구조화 데이터 필요
            )
        )

    # ── lat/lng null 비율 체크 ───────────────────────────────────────────────
    n = len(enriched)
    null_ratio = null_coord_count / n if n > 0 else 0.0

    if null_ratio >= 0.8:
        # 80% 이상이 null → fallback: 모두 포함
        logger.warning(
            "store_list_coords_mostly_null",
            extra={
                "extra_fields": {
                    "null_count": null_coord_count,
                    "total_fetched": n,
                    "null_ratio": round(null_ratio, 2),
                }
            },
        )
        items = enriched
    else:
        # 20% 이상이 좌표 있음 → null 좌표 매장 제외
        items = [s for s in enriched if s.lat is not None and s.lng is not None]

    return StoreListViewOut(items=items, total=total, limit=limit, offset=offset)
