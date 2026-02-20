"""
extract_solutions 엣지 케이스 테스트
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tasks.x_report_task import extract_solutions


# 엣지 케이스 1: 솔루션이 6개만 있는 경우
SAMPLE_INCOMPLETE = """
전략 카테고리 1: 테스트
- 솔루션 1: 첫 번째 솔루션
- 솔루션 2: 두 번째 솔루션

전략 카테고리 2: 테스트2
- 솔루션 1: 세 번째 솔루션
- 솔루션 2: 네 번째 솔루션

전략 카테고리 3: 테스트3
- 솔루션 1: 다섯 번째 솔루션
- 솔루션 2: 여섯 번째 솔루션
"""

# 엣지 케이스 2: 카테고리 없이 솔루션만 있는 경우 (Fallback 테스트)
SAMPLE_NO_CATEGORIES = """
- 솔루션 1: 첫 번째 솔루션입니다. 이것은 여러 줄로 된 설명입니다.
추가 설명이 계속됩니다.

- 솔루션 2: 두 번째 솔루션입니다.

- 솔루션 3: 세 번째 솔루션입니다.

- 솔루션 4: 네 번째 솔루션입니다.

- 솔루션 5: 다섯 번째 솔루션입니다.

- 솔루션 6: 여섯 번째 솔루션입니다.

- 솔루션 7: 일곱 번째 솔루션입니다.

- 솔루션 8: 여덟 번째 솔루션입니다.

- 솔루션 9: 아홉 번째 솔루션입니다.
"""

# 엣지 케이스 3: 영어 "Solution" 키워드 사용
SAMPLE_ENGLISH = """
Strategy Category 1: Customer Satisfaction

- Solution 1: Analyze customer feedback to improve service quality.
This is a detailed explanation that spans multiple lines.

- Solution 2: Enhance product offerings based on demand.

- Solution 3: Implement loyalty programs.

Strategy Category 2: Market Position

- Solution 1: Conduct competitive analysis.

- Solution 2: Optimize pricing strategy.

- Solution 3: Expand market reach.

Strategy Category 3: Operations

- Solution 1: Streamline workflows.

- Solution 2: Invest in technology.

- Solution 3: Train staff regularly.
"""


def test_incomplete():
    """불완전한 솔루션 (6개) - ValueError 예상"""
    print("\n" + "="*60)
    print("Test: Incomplete Solutions (6/9)")
    print("="*60)

    try:
        results = extract_solutions(SAMPLE_INCOMPLETE)
        print(f"FAIL: Should raise ValueError but got {len(results)} solutions")
        return False
    except ValueError as e:
        print(f"PASS: Correctly raised ValueError")
        print(f"  Message: {e}")
        return True
    except Exception as e:
        print(f"ERROR: Unexpected exception: {e}")
        return False


def test_fallback():
    """카테고리 없이 솔루션만 - Fallback 테스트"""
    print("\n" + "="*60)
    print("Test: Fallback (No Categories)")
    print("="*60)

    try:
        results = extract_solutions(SAMPLE_NO_CATEGORIES)
        print(f"SUCCESS: Fallback extracted {len(results)} solutions")

        if len(results) != 9:
            print(f"FAIL: Expected 9, got {len(results)}")
            return False

        print("PASS: Fallback strategy works correctly")
        return True

    except ValueError as e:
        print(f"FAIL: {e}")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_english():
    """영어 키워드 "Solution" 테스트"""
    print("\n" + "="*60)
    print("Test: English Keywords")
    print("="*60)

    try:
        results = extract_solutions(SAMPLE_ENGLISH)
        print(f"SUCCESS: Extracted {len(results)} solutions")

        if len(results) != 9:
            print(f"FAIL: Expected 9, got {len(results)}")
            return False

        print("PASS: English keywords work correctly")
        return True

    except ValueError as e:
        print(f"FAIL: {e}")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("Testing Edge Cases")
    print("="*60)

    test1 = test_incomplete()  # Should raise ValueError
    test2 = test_fallback()    # Should use fallback
    test3 = test_english()     # Should handle English

    print(f"\n{'='*60}")
    print("FINAL RESULT")
    print('='*60)

    if test1 and test2 and test3:
        print("ALL EDGE CASE TESTS PASSED")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
