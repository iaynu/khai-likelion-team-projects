"""
Store Grade Service — 전체 매장 종합점수 분포 기반 등급/상위% 계산.

등급 분포 (상위% 기준):
  S:  상위  0 ~  3% (최상위 3%)
  A:  상위  3 ~ 10%
  B:  상위 10 ~ 30%
  C:  상위 30 ~ 70% (중간층)
  D:  상위 70 ~ 90%
  E:  상위 90 ~ 97%
  F:  상위 97 ~ 100% (최하위 3%)

종합점수: 맛·서비스·분위기·가격·위생 5개 feature_score(0-1) 평균 × 100
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# feature_scores 키 중 종합점수 계산에 사용할 5개
_SCORE_KEYS = ["taste", "service", "atmosphere", "price_value", "cleanliness"]

# 등급 경계 (상위% 상한, 등급 레이블)
_GRADE_BOUNDARIES: List[Tuple[float, str]] = [
    (3,   "S"),
    (10,  "A"),
    (30,  "B"),
    (70,  "C"),
    (90,  "D"),
    (97,  "E"),
    (100, "F"),
]

# 모듈 레벨 캐시: 전체 매장 종합점수 목록 (내림차순 정렬)
_cached_scores: Optional[List[float]] = None


def _compute_avg_score(feature_scores: dict) -> Optional[float]:
    """feature_scores dict → 종합점수 (0-100). 유효 항목이 없으면 None."""
    values = []
    for key in _SCORE_KEYS:
        info = feature_scores.get(key)
        if isinstance(info, dict):
            v = info.get("score")
        else:
            v = info
        if isinstance(v, (int, float)):
            values.append(float(v))
    if not values:
        return None
    return round(sum(values) / len(values) * 100, 2)


def _load_all_scores() -> List[float]:
    """
    simulation/data/raw/split_by_store_id_ver5_/ 의 모든 JSON을 읽어
    각 매장의 종합점수를 내림차순으로 반환. 결과는 모듈 캐시에 저장.
    """
    global _cached_scores
    if _cached_scores is not None:
        return _cached_scores

    scores: List[float] = []
    try:
        base = Path(settings.SIMULATION_ENGINE_PATH)
        data_dir = base / "data" / "raw" / "split_by_store_id_ver5_"
        if not data_dir.is_dir():
            logger.warning("store_grade_dir_not_found: %s", data_dir)
            _cached_scores = scores
            return scores

        for json_file in data_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    d = json.load(f)
                fs = d.get("review_metrics", {}).get("feature_scores") or {}
                avg = _compute_avg_score(fs)
                if avg is not None:
                    scores.append(avg)
            except Exception:
                pass  # 개별 파일 오류는 무시

    except Exception as exc:
        logger.warning("store_grade_load_failed: %s", exc)

    scores.sort(reverse=True)  # 높은 점수 → 낮은 점수
    _cached_scores = scores
    logger.info("store_grade_cache_built: total=%d stores", len(scores))
    return scores


def get_grade_and_rank(avg_score: float) -> Tuple[str, int]:
    """
    종합점수(0-100) → (등급 문자열, 상위 퍼센트 정수).

    상위 퍼센트: 이 매장보다 높은 점수를 가진 매장의 비율(%).
    예) 상위 5% → rank_percent=5, 등급=A
    """
    all_scores = _load_all_scores()

    if not all_scores:
        # 데이터 없으면 점수 자체로 추정 (0-100 선형)
        rank_pct = max(0, min(100, round(100 - avg_score)))
    else:
        # 이 매장보다 높은 점수를 가진 매장 수
        better_count = sum(1 for s in all_scores if s > avg_score)
        rank_pct = round(better_count / len(all_scores) * 100)

    # 등급 결정
    grade = "F"
    for threshold, label in _GRADE_BOUNDARIES:
        if rank_pct < threshold:
            grade = label
            break

    return grade, rank_pct


def invalidate_cache() -> None:
    """테스트/개발용: 캐시 무효화."""
    global _cached_scores
    _cached_scores = None
