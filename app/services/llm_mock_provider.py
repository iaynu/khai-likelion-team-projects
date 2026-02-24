"""
Mock LLM Provider — generates deterministic X-Report output for testing.
Replace this module with a real LLM client when integrating production models.
"""
from __future__ import annotations
import hashlib
import random
import time
from typing import Any, Dict, List


_STRATEGY_TITLES = [
    # Group 1 – 매장 운영 최적화
    ["피크 시간대 직원 배치 최적화", "메뉴 구성 리뷰 및 시즌 메뉴 도입", "음식 준비 시간 단축을 위한 프로세스 개선", "고객 피드백 수집 체계 강화"],
    # Group 2 – 마케팅 & 고객 경험
    ["지역 기반 타겟 마케팅 캠페인 실시", "SNS 활용을 통한 브랜드 인식 제고", "단골 고객 로얼티 프로그램 구축", "리뷰 관리 및 부정적 피드백 대응 강화"],
    # Group 3 – 매출 성장 & 경쟁력
    ["주변 경쟁 업체 벤치마킹 및 차별화 전략", "부대 매출 채널 개발 (배달·포장)", "주중 저조 시간대 프로모션 기획", "장기적 매장 확장 및 신규 상권 분석"],
]

_STRATEGY_DESCRIPTIONS = [
    [
        "유동인구 및 연령대 데이터를 활용하여 피크 시간대 서비스 품질을 유지하기 위한 직원 배치 계획을 수립한다.",
        "리뷰 키워드와 경쟁 업체 메뉴 분석을 기반으로 시즌별 한정 메뉴를 도입하여 방문 유입율을 높인다.",
        "주문 후 대기 시간에 대한 부정적 피드백을 줄이기 위해 식재료 준비와 조리 동선을 최적화한다.",
        "앱 내 리뷰 수집과 매장 내 QR 피드백을 결합하여 실시간 고객 만족도를 관리한다.",
    ],
    [
        "지역 유동인구 연령별 비율을 기준으로 타겟 세그먼트를 설정하고 지역 SNS·전단 활용 캠페인을 수행한다.",
        "음식 사진·영상 콘텐츠를 활용한 인스타그램·블로그 마케팅을 강화하여 신규 고객 유입을 확대한다.",
        "단골 고객에게 포인트·쿠폰 제도를 도입하여 재방문율과 평균 매출액을 동시에 극대화한다.",
        "부정적 리뷰에 신속하고 정중한 답변을 달고, 개선 사항을 공개하여 브랜드 신뢰도를 강화한다.",
    ],
    [
        "주변 동일 업종 업체의 가격·메뉴·리뷰를 체계적으로 분석하여 차별화된 포지션을 수립한다.",
        "배달 앱과 테이크아웃 패키지를 강화하여 매장 외부 매출 채널을 확대한다.",
        "주중 저조 시간대에 한정 프로모션을 구축하여 유동인구 변동과 연관된 수요를 활용한다.",
        "현재 상권 포화도와 인구 동향을 기반으로 신규 매장 후보지 평가 프레임을 구축한다.",
    ],
]

_RADAR_SUBJECTS = ["맛", "서비스", "분위기", "가격", "위생"]

_RADAR_REASONS = [
    # 맛
    ["리뷰에서 음식 맛 관련 긍정 언급이 다수 확인됩니다.", "메뉴 다양성에 대한 고객 요구가 반영되지 않았습니다.", "계절 한정 메뉴 도입으로 방문 유입을 높일 수 있습니다."],
    # 서비스
    ["직원 친절도에 대한 긍정 피드백이 있습니다.", "주문 대기 시간에 대한 불만이 일부 확인됩니다.", "응대 서비스 개선 여지가 있습니다."],
    # 분위기
    ["인테리어 관련 긍정 리뷰가 다수 존재합니다.", "소음 및 혼잡도에 대한 불만이 있습니다.", "공간 활용 개선으로 분위기 향상이 가능합니다."],
    # 가격
    ["가성비에 대한 긍정 평가가 많습니다.", "가격 대비 양에 대한 만족도가 높습니다.", "주변 경쟁 업체 대비 가격 경쟁력이 있습니다."],
    # 위생
    ["청결도에 대한 긍정 리뷰가 있습니다.", "위생 관련 부정 리뷰가 일부 존재합니다.", "정기적인 위생 점검으로 평점 향상이 필요합니다."],
]

_KEYWORD_POOL = [
    {"text": "맛있어요", "sentiment": "positive"},
    {"text": "친절해요", "sentiment": "positive"},
    {"text": "가성비", "sentiment": "positive"},
    {"text": "재방문의사", "sentiment": "positive"},
    {"text": "분위기좋음", "sentiment": "positive"},
    {"text": "양많음", "sentiment": "positive"},
    {"text": "대기시간", "sentiment": "negative"},
    {"text": "주차불편", "sentiment": "negative"},
    {"text": "가격부담", "sentiment": "negative"},
    {"text": "위치좋음", "sentiment": "positive"},
    {"text": "청결함", "sentiment": "positive"},
    {"text": "맛보통", "sentiment": "neutral"},
]

_IMPACT_DELTAS = [
    [
        {"arrival_rate_delta": 0.06, "satisfaction_delta": 0.04, "churn_rate_delta": -0.02},
        {"arrival_rate_delta": 0.08, "satisfaction_delta": 0.05, "churn_rate_delta": -0.03},
        {"arrival_rate_delta": 0.03, "satisfaction_delta": 0.07, "churn_rate_delta": -0.04},
        {"arrival_rate_delta": 0.02, "satisfaction_delta": 0.09, "churn_rate_delta": -0.05},
    ],
    [
        {"arrival_rate_delta": 0.10, "satisfaction_delta": 0.03, "churn_rate_delta": -0.01},
        {"arrival_rate_delta": 0.07, "satisfaction_delta": 0.06, "churn_rate_delta": -0.02},
        {"arrival_rate_delta": 0.04, "satisfaction_delta": 0.08, "churn_rate_delta": -0.04},
        {"arrival_rate_delta": 0.03, "satisfaction_delta": 0.05, "churn_rate_delta": -0.03},
    ],
    [
        {"arrival_rate_delta": 0.05, "satisfaction_delta": 0.02, "churn_rate_delta": -0.01},
        {"arrival_rate_delta": 0.09, "satisfaction_delta": 0.04, "churn_rate_delta": -0.02},
        {"arrival_rate_delta": 0.11, "satisfaction_delta": 0.03, "churn_rate_delta": -0.01},
        {"arrival_rate_delta": 0.04, "satisfaction_delta": 0.01, "churn_rate_delta": -0.02},
    ],
]


def generate_x_report(merged_input: dict, prompt_id: str) -> Dict[str, Any]:
    """
    Deterministic mock: returns content_text, config_json (with 12 strategies), model_meta.
    """
    # deterministic seed from merged_input hash
    raw = str(merged_input).encode()
    seed = int(hashlib.md5(raw).hexdigest()[:8], 16)
    random.seed(seed)

    start = time.time()

    # Build strategies list (3 groups × 4 actions = 12, shuffled per group for variety)
    strategies: list[dict] = []
    for g_idx in range(3):
        group_items = [
            {
                "solution_group": g_idx + 1,
                "action_idx": a_idx + 1,
                "title": _STRATEGY_TITLES[g_idx][a_idx],
                "description": _STRATEGY_DESCRIPTIONS[g_idx][a_idx],
                "impact_mapping": _IMPACT_DELTAS[g_idx][a_idx],
            }
            for a_idx in range(4)
        ]
        random.shuffle(group_items)
        strategies.extend(group_items)

    content_text = (
        f"[X-Report | prompt={prompt_id}]\n"
        f"Merged input keys: {list(merged_input.keys())}\n"
        f"Market data present: {'market_sales_review' in merged_input and merged_input['market_sales_review'] is not None}\n"
        f"Population data present: {'population' in merged_input and merged_input['population'] is not None}\n"
        f"Total strategies generated: {len(strategies)}\n"
        f"Summary: 상권·매출·리뷰와 인구 데이터를 종합하여 3개 솔루션 그룹, 총 12개 전략을 도출했습니다."
    )

    elapsed = time.time() - start

    # Build radar_data (5 subjects, seeded scores 45–88)
    radar_data = []
    for i, subject in enumerate(_RADAR_SUBJECTS):
        score = random.randint(45, 88)
        reason = _RADAR_REASONS[i][random.randint(0, len(_RADAR_REASONS[i]) - 1)]
        radar_data.append({"subject": subject, "A": score, "fullMark": 100, "reason": reason})

    # Build keywords (6–8 from pool, seeded)
    num_kw = random.randint(6, 8)
    kw_indices = random.sample(range(len(_KEYWORD_POOL)), min(num_kw, len(_KEYWORD_POOL)))
    keywords = [_KEYWORD_POOL[i] for i in sorted(kw_indices)]

    config_json = {
        "prompt_id": prompt_id,
        "strategies": strategies,
        "radar_data": radar_data,
        "keywords": keywords,
        "generated_at_seed": seed,
    }

    model_meta = {
        "model": "mock-llm-v1",
        "tokens_in": random.randint(500, 2000),
        "tokens_out": random.randint(300, 1200),
        "cost_usd": round(random.uniform(0.001, 0.05), 6),
        "latency_ms": round(elapsed * 1000, 2),
    }

    return {
        "content_text": content_text,
        "config_json": config_json,
        "model_meta": model_meta,
    }
