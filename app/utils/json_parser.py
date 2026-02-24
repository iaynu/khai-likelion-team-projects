"""
JSON 파싱 유틸리티

공개 API:
    safe_json_loads(text)   → dict  (파싱 실패 시 JSONParseError)
    normalize_to_dict(val)  → Optional[dict]
    JSONParseError          → 커스텀 예외

safe_json_loads 전략:
    1) json.loads() 직접 시도
    2) 실패 시: 첫 '{' ~ 마지막 '}' 구간 추출 후 재시도
    3) 여전히 실패 시: JSONParseError 발생
"""
import json
from typing import Optional


class JSONParseError(Exception):
    """JSON 파싱 실패 커스텀 예외"""


def safe_json_loads(text: str) -> dict:
    """문자열을 dict 로 파싱한다.

    Args:
        text: JSON 문자열 (또는 JSON 을 포함한 혼합 텍스트)

    Returns:
        파싱된 dict

    Raises:
        JSONParseError: 파싱 불가능한 경우
    """
    if not isinstance(text, str):
        raise JSONParseError(f"Expected str, got {type(text).__name__}")

    # 1차 시도: 직접 파싱
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        raise JSONParseError(f"Parsed JSON is not a dict: {type(result).__name__}")
    except json.JSONDecodeError:
        pass

    # 2차 시도: 첫 '{' ~ 마지막 '}' 브라켓 추출
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise JSONParseError(f"Cannot parse JSON from text (length={len(text)})")


def normalize_to_dict(value: object) -> Optional[dict]:
    """다양한 입력 타입을 dict 또는 None 으로 정규화한다.

    - dict  → 그대로 반환
    - str   → safe_json_loads() 시도; 실패하면 None
    - None  → None
    - 기타  → None
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return safe_json_loads(value)
        except JSONParseError:
            return None
    return None
