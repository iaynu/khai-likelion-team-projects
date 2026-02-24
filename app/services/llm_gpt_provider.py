"""
GPT LLM Provider — real OpenAI gpt-5.2 for X-Report generation.

파이프라인:
  1) _call_analysis()  → 매장 데이터 심층 분석 (temperature=0.1)
  2) _call_report()    → 구조화 JSON 생성 (temperature=0.7, json_object 모드)
  3) _build_categories_flat() → config_json.categories (P1 포맷) 변환

반환 형식 (x_report_service.py와 호환):
  {content_text, config_json, model_meta}
  config_json: {prompt_id, categories (P1), radar_data, keywords, analysis_log}
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_MODEL = "gpt-5.2"
_API_TIMEOUT = 90.0  # OpenAI 클라이언트 타임아웃 (초)


def _get_client() -> AsyncOpenAI:
    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured in settings")
    return AsyncOpenAI(api_key=api_key, timeout=_API_TIMEOUT)


async def _call_analysis(client: AsyncOpenAI, store_name: str, store_data: Dict) -> str:
    """Step 1: 매장 데이터 심층 분석 (temperature=0.1)."""
    system_prompt = (
        "당신은 대한민국 최고의 'F&B 데이터 사이언티스트'입니다. "
        "RAW 데이터를 분석하여 매장의 치명적인 약점과 경쟁 우위를 발굴하는 것이 임무입니다."
    )

    # 풍부한 컨텍스트 구성
    review_metrics = store_data.get("review_metrics") or {}
    feature_scores = review_metrics.get("feature_scores") or {}
    overall = review_metrics.get("overall_sentiment") or {}
    market = store_data.get("market_analysis") or {}
    top_keywords = store_data.get("top_keywords") or []
    critical_feedback = store_data.get("critical_feedback") or []
    rag_context = store_data.get("rag_context") or ""

    user_prompt = (
        f"다음 데이터를 종합 분석하여 매장 현황 분석 결과를 도출하세요.\n\n"
        f"매장명: {store_name}\n"
        f"업종: {store_data.get('sector_name') or '정보 없음'}\n"
        f"지역: {store_data.get('area_code') or '정보 없음'}\n"
        f"종합 감성 점수: {overall.get('score', '정보 없음')} ({overall.get('label', '')})\n"
        f"항목별 점수 (0-1 범위): {json.dumps(feature_scores, ensure_ascii=False)}\n"
        f"주요 키워드: {', '.join(top_keywords) if top_keywords else '정보 없음'}\n"
        f"주요 비판 의견: {'; '.join(critical_feedback) if critical_feedback else '없음'}\n"
        f"리뷰 요약: {rag_context or '정보 없음'}\n"
        f"시장 분석: {json.dumps(market, ensure_ascii=False)}\n"
        f"매출 분석: {store_data.get('revenue_analysis') or '정보 없음'}\n"
        f"고객 분석: {store_data.get('customer_analysis') or '정보 없음'}\n"
        f"인구 데이터: {store_data.get('population_data') or '정보 없음'}\n\n"
        "'### 핵심 분석 결과' 헤더 이후에 맛·서비스·분위기·가격·위생 5개 항목을 각각 평가하고 "
        "주요 키워드와 개선 전략 방향을 작성하세요."
    )

    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content


async def _call_report(
    client: AsyncOpenAI,
    store_name: str,
    analysis: str,
    precomputed_radar: Optional[List[Dict]] = None,
) -> Tuple[Dict, Dict]:
    """Step 2: 구조화 JSON 보고서 생성 (temperature=0.7, json_object 모드).

    precomputed_radar가 있으면 A 값은 고정하고 GPT는 reason만 생성한다.
    """
    system_prompt = "당신은 외식업 성공을 돕는 데이터 기반 비즈니스 전략가 'X-report'입니다."

    # radar_data 스키마: 실제 점수가 있으면 A 값을 명시해 GPT가 그대로 사용하도록 지시
    if precomputed_radar:
        radar_schema_lines = []
        for item in precomputed_radar:
            radar_schema_lines.append(
                f'    {{"subject": "{item["subject"]}", "A": {item["A"]}, "fullMark": 100, "reason": "이 점수의 근거를 한국어로 작성"}}'
            )
        radar_schema = ",\n".join(radar_schema_lines)
        radar_rule = (
            "- radar_data: 위에 명시된 A 값을 그대로 사용하고, reason만 한국어로 작성 (A 값 변경 금지)\n"
        )
    else:
        radar_schema = (
            '    {"subject": "맛", "A": 75, "fullMark": 100, "reason": "분석 근거"},\n'
            '    {"subject": "서비스", "A": 60, "fullMark": 100, "reason": "분석 근거"},\n'
            '    {"subject": "분위기", "A": 80, "fullMark": 100, "reason": "분석 근거"},\n'
            '    {"subject": "가격", "A": 65, "fullMark": 100, "reason": "분석 근거"},\n'
            '    {"subject": "위생", "A": 70, "fullMark": 100, "reason": "분석 근거"}'
        )
        radar_rule = "- radar_data: 정확히 5개 (맛/서비스/분위기/가격/위생 순서), A는 0-100 정수, reason은 한국어 근거\n"

    user_prompt = (
        f"'{store_name}' 매장의 X-report를 아래 JSON 스키마에 맞춰 작성하세요.\n\n"
        f"[분석 결과]\n{analysis}\n\n"
        "[JSON 스키마]\n"
        "{\n"
        '  "categories": [\n'
        '    {"title": "전략 카테고리명", "solutions": ["솔루션 설명 1 (50자 이상)", "솔루션 설명 2", "솔루션 설명 3"]},\n'
        '    {"title": "전략 카테고리명", "solutions": ["솔루션 설명 1", "솔루션 설명 2", "솔루션 설명 3"]},\n'
        '    {"title": "전략 카테고리명", "solutions": ["솔루션 설명 1", "솔루션 설명 2", "솔루션 설명 3"]}\n'
        "  ],\n"
        '  "radar_data": [\n'
        f"{radar_schema}\n"
        "  ],\n"
        '  "keywords": [\n'
        '    {"text": "키워드", "sentiment": "positive"},\n'
        '    {"text": "키워드", "sentiment": "negative"},\n'
        '    {"text": "키워드", "sentiment": "neutral"}\n'
        "  ]\n"
        "}\n\n"
        "[규칙]\n"
        "- categories: 정확히 3개, 각 3개 솔루션 (총 9개), 각 솔루션 최소 50자\n"
        f"{radar_rule}"
        "- keywords: 6-10개, sentiment는 positive/negative/neutral 중 하나\n"
        "- JSON만 출력, 다른 텍스트 금지"
    )

    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    meta: Dict[str, Any] = {
        "model": response.model,
        "request_id": response.id,
        "worker": "api",
    }
    if response.usage:
        meta["prompt_tokens"] = response.usage.prompt_tokens
        meta["completion_tokens"] = response.usage.completion_tokens
        meta["total_tokens"] = response.usage.total_tokens

    return json.loads(response.choices[0].message.content), meta


def _build_categories_flat(report_json: Dict) -> List[Dict]:
    """GPT categories → x_report_view_service P1 포맷 (flat list) 변환."""
    result = []
    for cat in report_json.get("categories", [])[:3]:
        cat_title = str(cat.get("title") or "")
        for sol_text in cat.get("solutions", [])[:3]:
            sol_text = str(sol_text).strip()
            # 첫 문장을 제목으로 추출
            title = sol_text.split(".")[0].strip()
            if len(title) > 200:
                title = title[:200]
            if len(title) < 5:
                title = sol_text[:100]
            result.append(
                {
                    "category": cat_title,
                    "title": title,
                    "desc": sol_text,
                    "execution": sol_text,
                    "effect": "",
                }
            )
    return result


async def generate_x_report_gpt(
    store_name: str,
    store_data: Dict,
    prompt_id: str,
    precomputed_radar: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Real GPT-5.2 X-Report 생성.

    Args:
        precomputed_radar: feature_scores에서 직접 계산한 radar 점수.
                           제공 시 GPT의 A 값을 이 값으로 덮어쓴다 (reason만 GPT 생성).

    Returns:
        {content_text, config_json, model_meta}
        - config_json.categories: P1 포맷 (view service가 직접 사용)
        - config_json.radar_data: 5개 레이더 항목 (실제 점수 반영)
        - config_json.keywords: 6-10개 키워드
    """
    start = time.time()
    client = _get_client()

    # Step 1: 분석
    logger.info("gpt_analysis_start", extra={"extra_fields": {"store": store_name}})
    analysis = await _call_analysis(client, store_name, store_data)

    # Step 2: 구조화 보고서 (precomputed_radar 있으면 A 값 고정 지시)
    logger.info("gpt_report_start", extra={"extra_fields": {"store": store_name}})
    report_json, model_meta = await _call_report(client, store_name, analysis, precomputed_radar)

    elapsed_ms = round((time.time() - start) * 1000, 2)
    model_meta["latency_ms"] = elapsed_ms

    # radar_data: precomputed A 값이 있으면 GPT reason을 유지하되 A 값을 강제 적용
    gpt_radar = report_json.get("radar_data", [])
    if precomputed_radar:
        # GPT가 생성한 reason을 subject 기준으로 매핑
        reason_map = {item.get("subject"): item.get("reason", "") for item in gpt_radar}
        radar_data = [
            {
                "subject": item["subject"],
                "A": item["A"],
                "fullMark": 100,
                "reason": reason_map.get(item["subject"], ""),
                "avg": item.get("avg"),
            }
            for item in precomputed_radar
        ]
    else:
        radar_data = gpt_radar

    config_json: Dict[str, Any] = {
        "prompt_id": prompt_id,
        "categories": _build_categories_flat(report_json),
        "radar_data": radar_data,
        "keywords": report_json.get("keywords", []),
        "analysis_log": analysis,
    }

    logger.info(
        "gpt_x_report_generated",
        extra={
            "extra_fields": {
                "model": model_meta.get("model"),
                "total_tokens": model_meta.get("total_tokens"),
                "latency_ms": elapsed_ms,
                "solutions_count": len(config_json["categories"]),
                "radar_count": len(config_json["radar_data"]),
                "keywords_count": len(config_json["keywords"]),
            }
        },
    )

    return {
        "content_text": analysis,
        "config_json": config_json,
        "model_meta": model_meta,
    }
