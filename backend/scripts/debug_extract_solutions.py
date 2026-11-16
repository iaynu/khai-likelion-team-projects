"""
extract_solutions 함수 디버그 스크립트

목적: GPT 출력에서 9개 솔루션 추출 실패 원인 진단
- 재현 가능한 테스트 환경 제공
- Regex 매칭 실패 지점 상세 로그
- MULTILINE, DOTALL 플래그 영향 분석
"""

import sys
import re
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tasks.x_report_task import extract_solutions

# 샘플 데이터 직접 정의
SAMPLE_REPORT_1 = """
전략 카테고리 1: 고객 만족도 향상
- 솔루션 1: 고객 리뷰 분석을 통해 고객들이 불만을 가진 부분을 파악하고, 이를 개선하는 방안을 마련해보세요.
리뷰 데이터를 정기적으로 모니터링하여 실시간으로 고객의 목소리를 반영하는 시스템을 구축하면 고객 만족도를 크게 향상시킬 수 있습니다.
- 솔루션 2: 고객들이 만족했던 부분을 강화하고, 이를 마케팅에 활용해보세요.
긍정적인 리뷰에서 언급된 강점을 SNS 홍보 콘텐츠로 제작하여 신규 고객 유입을 촉진할 수 있습니다.
- 솔루션 3: 고객의 의견을 적극적으로 수렴하는 시스템을 구축해보세요. 이를 통해 고객들이 자신의 의견이 반영되는 것을 느끼게 하여, 고객 만족도를 향상시킬 수 있습니다.

전략 카테고리 2: 상권 분석 및 위치 경쟁력 강화
- 솔루션 1: 주변 업종 분포를 분석하여, 자신의 매장이 어떤 종류의 매장들과 경쟁하고 있는지 파악하고, 이에 따른 전략을 마련해보세요.
경쟁 업체의 가격대, 메뉴 구성, 서비스 수준을 벤치마킹하여 차별화 포인트를 찾을 수 있습니다.
- 솔루션 2: 상권의 활성도를 분석하여, 매장의 위치가 고객 유치에 적합한지 판단해보세요.
유동인구 데이터와 주요 고객층의 동선을 분석하여 최적의 영업 시간대를 설정할 수 있습니다.
- 솔루션 3: 이웃하는 경쟁 매장들의 서비스와 가격을 비교해보세요. 이를 통해 자신의 매장의 경쟁력을 높일 수 있는 방안을 생각해보세요.

전략 카테고리 3: 매장 운영 효율화
- 솔루션 1: 매장 내부의 업무 프로세스를 점검해보세요. 비효율적인 부분이 있다면 개선하여 업무 효율을 높여보세요.
주문 처리 시스템, 재고 관리, 직원 배치 등을 최적화하면 운영 비용을 절감할 수 있습니다.
- 솔루션 2: 재고 관리 시스템을 점검하고, 필요하다면 개선해보세요. 재고 부족이나 낭비를 최소화하면 매출 향상에 도움이 됩니다.
ABC 분석을 통해 핵심 재고 품목을 파악하고 자동 발주 시스템을 도입하는 것을 고려해보세요.
- 솔루션 3: 직원들의 업무 만족도를 점검해보세요. 만족도가 낮다면, 이를 개선하는 방안을 마련하여 직원들의 업무 효율을 높여보세요.
정기적인 교육 프로그램과 인센티브 제도를 통해 직원 몰입도를 향상시킬 수 있습니다.
"""

SAMPLE_REPORT_2 = """
**전략 카테고리 1: 브랜드 인지도 제고**

- **솔루션 1:** SNS 마케팅 강화를 통해 브랜드 노출을 극대화하세요.
Instagram, TikTok 등 시각적 플랫폼에서 매장의 시그니처 메뉴와 인테리어를 적극 홍보하여
젊은 층의 방문을 유도할 수 있습니다. 인플루언서 협업도 고려해보세요.

- **솔루션 2:** 로컬 커뮤니티 참여를 통해 지역 내 인지도를 높이세요.
동네 축제 참가, 지역 단체 후원 등을 통해 매장이 지역사회의 일원임을 알리고
단골 고객층을 확보할 수 있습니다.

- **솔루션 3:** 시그니처 메뉴 개발로 차별화된 브랜드 이미지를 구축하세요.
경쟁 매장과 구별되는 독창적인 메뉴를 개발하여 "이 메뉴는 여기서만 먹을 수 있다"는
인식을 심어줄 수 있습니다.

**전략 카테고리 2: 가격 경쟁력 확보**

- **솔루션 1:** 원가 분석을 통해 불필요한 비용을 절감하세요.
식재료 구매처 다변화, 재고 회전율 향상 등을 통해 원가율을 낮추고
가격 경쟁력을 확보할 수 있습니다.

- **솔루션 2:** 세트 메뉴 및 프로모션 전략으로 가성비 이미지를 강화하세요.
점심 특선, 해피아워 할인 등 시간대별 프로모션을 통해 고객 유입을 늘리고
객단가는 유지하면서도 합리적인 가격이라는 인식을 심어줄 수 있습니다.

- **솔루션 3:** 멤버십 프로그램으로 단골 고객에게 가격 혜택을 제공하세요.
재방문 고객에게 포인트 적립, 할인 쿠폰 제공 등의 혜택을 주어
충성도를 높이고 장기적인 매출 안정화를 도모할 수 있습니다.

**전략 카테고리 3: 서비스 품질 향상**

- **솔루션 1:** 직원 교육 프로그램을 체계화하여 서비스 수준을 표준화하세요.
친절한 응대, 신속한 서비스, 청결 관리 등에 대한 매뉴얼을 만들고
정기적인 교육을 실시하여 일관된 서비스 품질을 유지할 수 있습니다.

- **솔루션 2:** 고객 불편사항 즉시 대응 시스템을 구축하세요.
현장에서 발생하는 문제를 실시간으로 파악하고 빠르게 해결하여
부정적인 경험이 부정적인 리뷰로 이어지는 것을 방지할 수 있습니다.

- **솔루션 3:** 매장 청결도를 최우선 과제로 관리하세요.
주방과 홀의 위생 상태를 수시로 점검하고, 고객이 눈으로 확인할 수 있는
청결함을 유지하여 신뢰도를 높일 수 있습니다.
"""


def analyze_regex_behavior(report_text: str, sample_name: str):
    """
    Regex 패턴 매칭 동작 상세 분석
    """
    print(f"\n{'='*80}")
    print(f"📊 REGEX 분석: {sample_name}")
    print(f"{'='*80}\n")

    # 현재 extract_solutions에서 사용하는 패턴들
    CATEGORY_PATTERN = r"### 🔹 전략 카테고리 \d+[:.]\s*\*{0,2}\[?(.*?)\]?\*{0,2}\s*(?:\(.*?\))?\s*\n"
    SOLUTION_PATTERN = r"-\s*\*{0,2}솔루션 [A-Z][:.]\*{0,2}\s*(.*?)(?=\n-\s*\*{0,2}솔루션 [A-Z]|### |## |$)"

    print("🔍 1. 카테고리 분할 Regex 테스트")
    print(f"패턴: {CATEGORY_PATTERN}")
    print()

    # re.split 실행 (플래그 없음)
    categories_no_flag = re.split(CATEGORY_PATTERN, report_text)
    print(f"✅ re.split() 결과 (플래그 없음):")
    print(f"   분할된 조각 개수: {len(categories_no_flag)}")

    if len(categories_no_flag) == 1:
        print("   ❌ 분할 실패! 원본 텍스트가 그대로 반환됨")
        print(f"   원인: 패턴이 텍스트의 어떤 부분과도 매칭되지 않음")
    else:
        print(f"   ✅ 분할 성공! {len(categories_no_flag)} 조각으로 분할됨")
        for i, chunk in enumerate(categories_no_flag[:5]):  # 처음 5개만 출력
            print(f"   [{i}]: {chunk[:100]}..." if len(chunk) > 100 else f"   [{i}]: {chunk}")

    print()

    # 실제 카테고리 헤더 찾기 (실제 GPT 출력 패턴)
    ACTUAL_CATEGORY_PATTERN = r"^(?:\*{0,2})전략 카테고리 \d+[:.]\s*(.+?)$"

    print("🔍 2. 실제 GPT 출력 패턴 테스트")
    print(f"패턴: {ACTUAL_CATEGORY_PATTERN}")
    print()

    # MULTILINE 플래그 없이
    matches_no_multiline = re.findall(ACTUAL_CATEGORY_PATTERN, report_text)
    print(f"✅ re.findall() 결과 (플래그 없음):")
    print(f"   매칭 개수: {len(matches_no_multiline)}")
    if len(matches_no_multiline) == 0:
        print("   ❌ 매칭 실패! ^, $ 앵커가 문자열 전체의 시작/끝만 매칭")

    # MULTILINE 플래그 사용
    matches_with_multiline = re.findall(ACTUAL_CATEGORY_PATTERN, report_text, re.MULTILINE)
    print(f"\n✅ re.findall() 결과 (re.MULTILINE):")
    print(f"   매칭 개수: {len(matches_with_multiline)}")
    if len(matches_with_multiline) > 0:
        print("   ✅ 매칭 성공! ^, $ 앵커가 각 줄의 시작/끝 매칭")
        for i, match in enumerate(matches_with_multiline, 1):
            print(f"   카테고리 {i}: {match}")

    print()

    print("🔍 3. 솔루션 추출 Regex 테스트")
    print(f"패턴: {SOLUTION_PATTERN}")
    print()

    # 첫 번째 카테고리 내용 추출 (수동)
    lines = report_text.split('\n')
    category_content = []
    in_first_category = False

    for line in lines:
        if '전략 카테고리 1' in line:
            in_first_category = True
            continue
        if in_first_category:
            if '전략 카테고리 2' in line or '전략 카테고리 3' in line:
                break
            category_content.append(line)

    first_category_text = '\n'.join(category_content)

    # 솔루션 패턴 매칭 (기존 패턴 - 알파벳)
    solution_matches_alpha = re.findall(SOLUTION_PATTERN, first_category_text, re.DOTALL)
    print(f"✅ 첫 번째 카테고리에서 솔루션 추출 ([A-Z] 패턴):")
    print(f"   매칭 개수: {len(solution_matches_alpha)}")
    if len(solution_matches_alpha) == 0:
        print("   ❌ 매칭 실패! '솔루션 A', '솔루션 B' 등을 찾았지만 실제는 숫자 사용")

    # 솔루션 패턴 매칭 (수정된 패턴 - 숫자)
    SOLUTION_PATTERN_DIGIT = r"-\s*\*{0,2}솔루션 \d+[:.]\*{0,2}\s*(.*?)(?=\n-\s*\*{0,2}솔루션 \d+|전략 카테고리|$)"
    solution_matches_digit = re.findall(SOLUTION_PATTERN_DIGIT, first_category_text, re.DOTALL)
    print(f"\n✅ 첫 번째 카테고리에서 솔루션 추출 (\\d+ 패턴):")
    print(f"   매칭 개수: {len(solution_matches_digit)}")
    if len(solution_matches_digit) > 0:
        print("   ✅ 매칭 성공! 숫자 패턴으로 솔루션 발견")
        for i, sol in enumerate(solution_matches_digit, 1):
            preview = sol[:100].replace('\n', ' ').strip()
            print(f"   솔루션 {i}: {preview}...")

    print()

    print("🔍 4. 매칭 실패 지점 상세 분석")
    print()

    # 카테고리 헤더 라인 찾기
    category_lines = []
    for i, line in enumerate(lines, 1):
        if '전략 카테고리' in line:
            category_lines.append((i, line))

    print(f"✅ 텍스트에서 '전략 카테고리' 포함 라인:")
    for line_num, line in category_lines:
        print(f"   라인 {line_num}: {line.strip()}")

    print()
    print(f"✅ 기대했던 패턴 vs 실제 패턴:")
    print(f"   기대: ### 🔹 전략 카테고리 1: [고객 만족도 향상]")
    print(f"   실제: {category_lines[0][1].strip() if category_lines else 'N/A'}")
    print()
    print(f"   차이점:")
    print(f"   1. ❌ '### 🔹' 접두사 없음")
    print(f"   2. ❌ 대괄호 [] 없음")
    print(f"   3. ⚠️  Bold(**) 마크다운 사용 여부는 샘플마다 다름")

    print()

    # 솔루션 라인 찾기
    solution_lines = []
    for i, line in enumerate(lines, 1):
        if '솔루션' in line and line.strip().startswith('-'):
            solution_lines.append((i, line))

    print(f"✅ 텍스트에서 '솔루션' 포함 라인 (최대 5개):")
    for line_num, line in solution_lines[:5]:
        print(f"   라인 {line_num}: {line.strip()}")

    print()
    print(f"✅ 기대했던 패턴 vs 실제 패턴:")
    print(f"   기대: - 솔루션 A: 고객 리뷰 분석을...")
    print(f"   실제: {solution_lines[0][1].strip() if solution_lines else 'N/A'}")
    print()
    print(f"   차이점:")
    print(f"   1. ❌ 알파벳(A, B, C) 대신 숫자(1, 2, 3) 사용")
    print(f"   2. ⚠️  Bold(**) 마크다운 사용 여부는 샘플마다 다름")

    print()


def test_extract_solutions(report_text: str, sample_name: str, expected_count: int = 9):
    """
    extract_solutions 함수 테스트 및 결과 검증
    """
    print(f"\n{'='*80}")
    print(f"Test: {sample_name}")
    print(f"{'='*80}\n")

    # extract_solutions 실행
    results = extract_solutions(report_text)

    print(f"Extraction Result:")
    print(f"   Extracted: {len(results)} solutions")
    print(f"   Expected: {expected_count} solutions")
    print()

    if len(results) != expected_count:
        print(f"FAILED!")
        print(f"   Reason: Count mismatch (actual: {len(results)}, expected: {expected_count})")
        print()

        if len(results) == 0:
            print("Debug Hints:")
            print("   1. re.split() failed to split categories")
            print("   2. Pattern mismatch")
        else:
            print(f"Partial success: {len(results)} extracted")
            print("   Solutions:")
            for i, sol in enumerate(results, 1):
                print(f"   {i}. [Group {sol['solution_group']}] {sol['title'][:50]}...")

        return False
    else:
        print(f"PASSED!")
        print()
        print("Extracted Solutions:")
        for i, sol in enumerate(results, 1):
            print(f"   {i}. [Group {sol['solution_group']}] {sol['title'][:60]}...")
        return True


def main():
    """
    메인 디버그 실행
    """
    print("\n" + "="*80)
    print("extract_solutions Debug Script")
    print("="*80)
    print()
    print("Goal: Test 9 solutions extraction")
    print()

    # 실제 extract_solutions 테스트
    test1_passed = test_extract_solutions(SAMPLE_REPORT_1, "SAMPLE_REPORT_1", expected_count=9)
    test2_passed = test_extract_solutions(SAMPLE_REPORT_2, "SAMPLE_REPORT_2", expected_count=9)

    # 최종 결과
    print(f"\n{'='*80}")
    print("FINAL RESULT")
    print(f"{'='*80}\n")

    if test1_passed and test2_passed:
        print("ALL TESTS PASSED")
        print("extract_solutions correctly extracts 9 solutions.")
        sys.exit(0)
    else:
        print("TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
