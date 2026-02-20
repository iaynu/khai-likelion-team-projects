"""
에러 처리 로직 테스트 스크립트

목적:
1. format_error_message 함수 테스트
2. 에러 코드 표준화 검증
3. 길이 제한 검증
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tasks.x_report_task import format_error_message


def test_format_error_message():
    """format_error_message 함수 테스트"""
    print("\n" + "="*80)
    print("Test: format_error_message")
    print("="*80)

    # Test 0: Context 없을 때 "| Context:" 생략 확인
    print("\n[Test 0] No context - should omit '| Context:' part")
    result = format_error_message(
        error_code="TEST_NO_CONTEXT",
        message="Error message without context"
    )
    print(f"Result: {result}")
    assert "| Context:" not in result
    assert result == "[TEST_NO_CONTEXT] Error message without context"
    print("PASS")

    # Test 1: 기본 포맷
    print("\n[Test 1] Basic format")
    result = format_error_message(
        error_code="STRATEGY_PARSE_COUNT_MISMATCH",
        message="Expected 9 solutions, got 6"
    )
    print(f"Result: {result}")
    assert result.startswith("[STRATEGY_PARSE_COUNT_MISMATCH]")
    assert "Expected 9 solutions, got 6" in result
    print("PASS")

    # Test 2: Context 포함
    print("\n[Test 2] With context")
    result = format_error_message(
        error_code="STRATEGY_PARSE_COUNT_MISMATCH",
        message="Expected 9 solutions, got 6",
        context="GPT output sample text here..."
    )
    print(f"Result: {result}")
    assert "| Context:" in result
    assert "GPT output sample text" in result
    print("PASS")

    # Test 3: 메시지 길이 제한 (500자)
    print("\n[Test 3] Message length limit (500 chars)")
    long_message = "A" * 600
    result = format_error_message(
        error_code="TEST",
        message=long_message
    )
    print(f"Message length: {len(result)}")
    assert len(result) <= 1000  # 전체 길이 제한
    # [TEST] 부분 + 500자 메시지
    expected_len = len("[TEST] ") + 500
    assert len(result) == expected_len
    print("PASS")

    # Test 4: Context 길이 제한 (500자)
    print("\n[Test 4] Context length limit (500 chars)")
    long_context = "B" * 600
    result = format_error_message(
        error_code="TEST",
        message="Short message",
        context=long_context
    )
    print(f"Result length: {len(result)}")
    assert len(result) <= 1000
    assert "| Context:" in result
    # Context는 500자로 잘림
    assert result.count("B") <= 500
    print("PASS")

    # Test 5: 전체 길이 제한 (1000자)
    print("\n[Test 5] Total length limit (1000 chars)")
    result = format_error_message(
        error_code="TEST",
        message="M" * 600,
        context="C" * 600
    )
    print(f"Result length: {len(result)}")
    assert len(result) == 1000  # 정확히 1000자로 잘림
    print("PASS")

    # Test 6: Traceback truncate 검증 (UNKNOWN_ERROR 시나리오)
    print("\n[Test 6] Traceback truncate verification")

    # 의도적으로 긴 traceback 생성
    def deep_function_5():
        raise Exception("Deep error with very long message " + "X" * 500)

    def deep_function_4():
        deep_function_5()

    def deep_function_3():
        deep_function_4()

    def deep_function_2():
        deep_function_3()

    def deep_function_1():
        deep_function_2()

    try:
        deep_function_1()
    except Exception as e:
        import traceback
        full_traceback = traceback.format_exc()
        print(f"Original traceback length: {len(full_traceback)} chars")

        # format_error_message에 긴 traceback 전달
        result = format_error_message(
            error_code="UNKNOWN_ERROR",
            message=f"Unexpected error: {type(e).__name__} - {str(e)[:100]}",
            context=full_traceback  # 긴 traceback 전달
        )

        print(f"Formatted message length: {len(result)} chars")
        assert len(result) <= 1000, f"Message too long: {len(result)} > 1000"
        assert result.startswith("[UNKNOWN_ERROR]")
        assert "| Context:" in result
        print("PASS: Traceback correctly truncated to <= 1000 chars")

    print("\n" + "="*80)
    print("All format_error_message tests PASSED")
    print("="*80)


def test_error_codes():
    """에러 코드 표준화 검증"""
    print("\n" + "="*80)
    print("Test: Error Code Standards")
    print("="*80)

    error_codes = {
        "STRATEGY_PARSE_COUNT_MISMATCH": "Expected 9 solutions, got X",
        "BACKEND_TIMEOUT": "Backend API timeout",
        "BACKEND_API_ERROR": "Backend API HTTP error",
        "LLM_API_ERROR": "OpenAI API error",
        "UNKNOWN_ERROR": "Unexpected error"
    }

    for code, desc in error_codes.items():
        result = format_error_message(code, desc)
        print(f"\n[{code}]")
        print(f"  Sample: {result[:100]}...")
        assert result.startswith(f"[{code}]")

    print("\n" + "="*80)
    print("All error code standards PASSED")
    print("="*80)


def test_real_scenarios():
    """실제 시나리오 테스트"""
    print("\n" + "="*80)
    print("Test: Real Scenarios")
    print("="*80)

    # Scenario 1: ValueError - Strategy parsing failed
    print("\n[Scenario 1] ValueError: Strategy parsing failed")
    report_text = "전략 카테고리 1: 테스트\n- 솔루션 1: 첫 번째\n- 솔루션 2: 두 번째"
    error_msg = format_error_message(
        error_code="STRATEGY_PARSE_COUNT_MISMATCH",
        message="Expected 9 solutions, got 2. Report parsing failed. Please check GPT output format.",
        context=report_text[:500]
    )
    print(f"Error message: {error_msg[:150]}...")
    assert "[STRATEGY_PARSE_COUNT_MISMATCH]" in error_msg
    assert "Expected 9 solutions, got 2" in error_msg
    assert "Context:" in error_msg
    print("PASS")

    # Scenario 2: Timeout
    print("\n[Scenario 2] Backend Timeout")
    error_msg = format_error_message(
        error_code="BACKEND_TIMEOUT",
        message="Backend API timeout: ReadTimeout('Request timed out')",
        context="URL: http://127.0.0.1:8000/x-reports/save"
    )
    print(f"Error message: {error_msg[:150]}...")
    assert "[BACKEND_TIMEOUT]" in error_msg
    assert "Backend API timeout" in error_msg
    print("PASS")

    # Scenario 3: HTTPError
    print("\n[Scenario 3] Backend API Error")
    error_msg = format_error_message(
        error_code="BACKEND_API_ERROR",
        message="Backend API error: 500 - Internal Server Error",
        context="URL: http://127.0.0.1:8000/x-reports/save"
    )
    print(f"Error message: {error_msg[:150]}...")
    assert "[BACKEND_API_ERROR]" in error_msg
    assert "500" in error_msg
    print("PASS")

    # Scenario 4: OpenAI APIError
    print("\n[Scenario 4] OpenAI API Error")
    error_msg = format_error_message(
        error_code="LLM_API_ERROR",
        message="OpenAI API error: RateLimitError('Rate limit exceeded')",
        context="Type: RateLimitError"
    )
    print(f"Error message: {error_msg[:150]}...")
    assert "[LLM_API_ERROR]" in error_msg
    assert "OpenAI API error" in error_msg
    print("PASS")

    # Scenario 5: Unknown Error
    print("\n[Scenario 5] Unknown Error")
    import traceback
    stack_trace = "Traceback (most recent call last):\n  File 'test.py', line 10, in <module>\n    raise Exception('Test')\nException: Test"
    error_msg = format_error_message(
        error_code="UNKNOWN_ERROR",
        message="Unexpected error: Exception - Test",
        context=stack_trace[:500]
    )
    print(f"Error message: {error_msg[:150]}...")
    assert "[UNKNOWN_ERROR]" in error_msg
    assert "Unexpected error" in error_msg
    print("PASS")

    print("\n" + "="*80)
    print("All real scenario tests PASSED")
    print("="*80)


if __name__ == "__main__":
    try:
        test_format_error_message()
        test_error_codes()
        test_real_scenarios()

        print("\n" + "="*80)
        print("ALL TESTS PASSED")
        print("="*80)
        sys.exit(0)

    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
