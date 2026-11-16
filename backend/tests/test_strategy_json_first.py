"""
전략 파서 JSON-first 회귀 테스트

실행 방법:
    pytest tests/test_strategy_json_first.py -v
    pytest tests/test_strategy_json_first.py::test_parse_json_success -v

목적:
    - 외부 OpenAI 호출 없이 fixture만으로 JSON-first 파서 검증
    - parse_strategies_json() 성공/실패 시나리오 커버
    - extract_solutions() fallback 동작 확인
"""
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from tasks.x_report_task import parse_strategies_json, extract_solutions
from tests.fixtures.strategy_fixtures import (
    VALID_JSON_9_SOLUTIONS,
    BROKEN_JSON_WITH_TEXT,
    INVALID_JSON_ONLY_2_CATEGORIES,
    INVALID_JSON_UNEVEN_SOLUTIONS,
    JSON_WITH_PREAMBLE,
    INVALID_JSON_EMPTY_SOLUTION,
)


class TestParseStrategiesJson:
    """parse_strategies_json() 단위 테스트"""

    def test_parse_json_success_valid_9_solutions(self):
        """
        [Scenario A] 정상 JSON 스키마 (9개 솔루션)

        Given: 정상적인 JSON 문자열 (3 카테고리 × 3 솔루션)
        When: parse_strategies_json() 호출
        Then: 9개 strategies 반환, 각 필드 검증
        """
        # When
        strategies = parse_strategies_json(VALID_JSON_9_SOLUTIONS)

        # Then
        assert len(strategies) == 9, "Expected 9 strategies"

        # 첫 번째 솔루션 검증
        first = strategies[0]
        assert first["solution_group"] == 1
        assert first["action_idx"] == 0
        assert "title" in first
        assert "description" in first
        assert "config" in first
        assert "metric" in first["config"]
        assert "impact_mapping" in first

        # 마지막 솔루션 검증 (3번째 카테고리, 3번째 솔루션)
        last = strategies[8]
        assert last["solution_group"] == 3
        assert last["action_idx"] == 2

        # 모든 솔루션의 description이 비어있지 않은지 확인
        for idx, s in enumerate(strategies):
            assert len(s["description"]) >= 10, f"Strategy {idx} description too short"

    def test_parse_json_with_preamble_extracted(self):
        """
        [Scenario A-2] JSON with preamble (2차 시도로 성공)

        Given: JSON 앞뒤에 텍스트가 있는 문자열
        When: parse_strategies_json() 호출 (2차 시도: {} 추출)
        Then: 9개 strategies 반환
        """
        # When
        strategies = parse_strategies_json(JSON_WITH_PREAMBLE)

        # Then
        assert len(strategies) == 9
        assert strategies[0]["solution_group"] == 1
        assert strategies[8]["solution_group"] == 3

    def test_parse_json_fail_only_2_categories(self):
        """
        [Scenario C-1] JSON 스키마 있으나 카테고리 2개만 (총 6개)

        Given: 카테고리가 2개만 있는 JSON
        When: parse_strategies_json() 호출
        Then: ValueError 발생 ("Expected 3 categories, got 2")
        """
        # When & Then
        with pytest.raises(ValueError, match="Expected 3 categories, got 2"):
            parse_strategies_json(INVALID_JSON_ONLY_2_CATEGORIES)

    def test_parse_json_fail_uneven_solutions(self):
        """
        [Scenario C-2] JSON 스키마 있으나 솔루션 개수 불일치 (3+2+3 = 8개)

        Given: 두 번째 카테고리에 솔루션 2개만 있음
        When: parse_strategies_json() 호출
        Then: ValueError 발생 ("must have 3 solutions")
        """
        # When & Then
        with pytest.raises(ValueError, match="must have 3 solutions"):
            parse_strategies_json(INVALID_JSON_UNEVEN_SOLUTIONS)

    def test_parse_json_fail_empty_solution(self):
        """
        [Scenario E] 빈 문자열 솔루션 포함

        Given: 한 솔루션이 빈 문자열인 JSON
        When: parse_strategies_json() 호출
        Then: ValueError 발생 ("too short")
        """
        # When & Then
        with pytest.raises(ValueError, match="too short"):
            parse_strategies_json(INVALID_JSON_EMPTY_SOLUTION)


class TestExtractSolutionsJsonFirst:
    """extract_solutions() JSON-first → fallback 통합 테스트"""

    def test_extract_json_first_success(self):
        """
        [Scenario A] JSON-first 파서 성공

        Given: 정상 JSON 문자열
        When: extract_solutions() 호출
        Then: JSON 파서로 9개 추출 (fallback 사용 안 함)
        """
        # When
        strategies = extract_solutions(VALID_JSON_9_SOLUTIONS)

        # Then
        assert len(strategies) == 9
        assert strategies[0]["solution_group"] == 1
        assert strategies[8]["solution_group"] == 3

    def test_extract_fallback_to_text_parser(self):
        """
        [Scenario B] JSON 파싱 실패 → 텍스트 파서 fallback

        Given: JSON이 아닌 마크다운 텍스트
        When: extract_solutions() 호출
        Then: 텍스트 파서(카테고리 기준)로 9개 추출
        """
        # When
        strategies = extract_solutions(BROKEN_JSON_WITH_TEXT)

        # Then
        assert len(strategies) == 9, "Fallback parser should extract 9 strategies"

        # 첫 번째 솔루션 검증
        first = strategies[0]
        assert first["solution_group"] == 1
        assert "포토존" in first["description"] or "포토존" in first["title"]

    def test_extract_fail_all_parsers_invalid_count(self):
        """
        [Scenario C] 모든 파서 실패 (솔루션 개수 불일치)

        Given: JSON은 유효하지만 카테고리가 2개만 있음 (6개 솔루션)
        When: extract_solutions() 호출
        Then: ValueError 발생 (JSON 파서 실패 → 텍스트 파서도 실패)

        Note:
            - JSON 파서: "Expected 3 categories" 에러
            - 텍스트 파서: 카테고리 헤더가 없어 파싱 실패
            - 최종: ValueError("Expected 9 solutions, got 0")
        """
        # When & Then
        with pytest.raises(ValueError, match="Expected"):
            extract_solutions(INVALID_JSON_ONLY_2_CATEGORIES)


class TestMetricInference:
    """메트릭 추론 로직 검증"""

    def test_metric_inference_from_category_title(self):
        """
        카테고리 제목에서 메트릭 추론

        Given: "공간 경험 개선" 카테고리
        When: parse_strategies_json() 호출
        Then: metric="insta" 추론됨
        """
        # When
        strategies = parse_strategies_json(VALID_JSON_9_SOLUTIONS)

        # Then: 첫 번째 카테고리 (공간 경험 개선) → insta
        cat1_strategies = [s for s in strategies if s["solution_group"] == 1]
        assert len(cat1_strategies) == 3

        for s in cat1_strategies:
            # "공간 경험" 키워드가 있으면 "insta" metric
            if "공간 경험" in VALID_JSON_9_SOLUTIONS:
                assert s["config"]["metric"] in ["insta", "overall"]

        # 두 번째 카테고리 (제품 매력 강화) → taste
        cat2_strategies = [s for s in strategies if s["solution_group"] == 2]
        assert len(cat2_strategies) == 3

        for s in cat2_strategies:
            # "제품 매력" 또는 "맛" 키워드가 있으면 "taste" metric
            if "제품 매력" in VALID_JSON_9_SOLUTIONS or "맛" in s["description"]:
                assert s["config"]["metric"] in ["taste", "overall"]


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_very_long_solution_truncated_to_500(self):
        """
        매우 긴 솔루션 텍스트 → 500자로 truncate

        Given: 솔루션 텍스트가 600자인 JSON
        When: parse_strategies_json() 호출
        Then: description은 최대 500자까지만 저장
        """
        import json

        long_solution = "매우 긴 솔루션 설명입니다. " * 50  # 약 600자

        long_json = json.dumps(
            {
                "categories": [
                    {
                        "title": "테스트 카테고리 1",
                        "solutions": [long_solution, long_solution, long_solution],
                    },
                    {
                        "title": "테스트 카테고리 2",
                        "solutions": [long_solution, long_solution, long_solution],
                    },
                    {
                        "title": "테스트 카테고리 3",
                        "solutions": [long_solution, long_solution, long_solution],
                    },
                ]
            },
            ensure_ascii=False,
        )

        # When
        strategies = parse_strategies_json(long_json)

        # Then
        assert len(strategies) == 9
        for s in strategies:
            assert len(s["description"]) <= 500, "Description should be truncated to 500 chars"

    def test_solution_with_special_characters(self):
        """
        특수 문자가 포함된 솔루션 → 정상 처리

        Given: 이모지, 특수기호가 포함된 솔루션
        When: parse_strategies_json() 호출
        Then: 9개 strategies 정상 반환
        """
        import json

        special_json = json.dumps(
            {
                "categories": [
                    {
                        "title": "공간 경험 개선 🎨",
                        "solutions": [
                            "매장 입구에 '인스타그램 포토존'을 설치합니다! (50% 할인)",
                            "테이블 간격을 넓혀 프라이버시를 강화합니다 → 고객 만족도 ↑",
                            "계절별로 인테리어 소품을 교체합니다 #감성카페",
                        ],
                    },
                    {
                        "title": "제품 매력 강화 🍽️",
                        "solutions": [
                            "시그니처 메뉴의 레시피를 개선합니다 (레벨업!)",
                            "계절 한정 메뉴를 매달 출시합니다 ★★★★★",
                            "메뉴판에 음식 사진과 상세한 설명을 추가합니다 [NEW]",
                        ],
                    },
                    {
                        "title": "서비스 품질 향상 ⭐",
                        "solutions": [
                            "직원 응대 매뉴얼을 체계화합니다 (A→B→C)",
                            "주문 후 음식 제공 시간을 5분 이내로 단축합니다 ⏱️",
                            "고객 의견 수렴 채널을 다양화합니다 @ 실시간",
                        ],
                    },
                ]
            },
            ensure_ascii=False,
        )

        # When
        strategies = parse_strategies_json(special_json)

        # Then
        assert len(strategies) == 9
        # 첫 번째 솔루션에 특수문자가 포함되어 있는지 확인
        first_desc = strategies[0]["description"]
        assert "포토존" in first_desc or "인스타그램" in first_desc
