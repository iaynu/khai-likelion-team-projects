"""
app/utils/serialization.py

JSON-serializable 변환 유틸.

to_jsonable(obj)
  임의의 Python 객체를 json.dumps(..., ensure_ascii=False) 가능한 타입으로
  재귀 변환합니다. numpy / pandas / dataclass / pydantic 등
  시뮬레이션 엔진이 반환할 수 있는 모든 타입을 커버합니다.

변환 규칙 (우선순위 순):
  None                    -> None
  bool                    -> bool          (int 서브클래스이므로 먼저 체크)
  int                     -> int
  str                     -> str
  float NaN/Inf           -> None          (json 미지원)
  float                   -> float
  dict                    -> {str(k): to_jsonable(v)}
  list / tuple            -> [to_jsonable(v)]
  dataclass instance      -> dataclasses.asdict() -> to_jsonable
  pydantic BaseModel      -> .model_dump() / .dict() -> to_jsonable
  numpy bool_             -> bool
  numpy integer           -> int
  numpy floating NaN/Inf  -> None
  numpy floating          -> float
  numpy complexfloating   -> str           (복소수 직렬화 불가)
  numpy ndarray           -> list -> to_jsonable
  numpy generic (기타)    -> .item() -> to_jsonable
  pandas DataFrame        -> records list -> to_jsonable
  pandas Series           -> list -> to_jsonable
  pandas NA / NaT         -> None
  datetime / date         -> ISO 8601 str
  Path                    -> str
  Enum                    -> .value -> to_jsonable
  bytes                   -> base64 str    (바이너리 손실 없이 보존)
  기타                     -> str(obj)     (데이터 유실 최소화)
"""
from __future__ import annotations

import base64
import dataclasses
import math
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """
    임의의 Python 객체를 json.dumps() 가능한 타입으로 재귀 변환합니다.

    Args:
        obj: 변환할 객체 (어떤 타입이든 허용)

    Returns:
        None / bool / int / float / str / list / dict 중 하나.
        json.dumps(result, ensure_ascii=False) 가 항상 성공함을 보장합니다.

    Notes:
        - NaN / Inf -> None (JSON 표준 미지원)
        - dict 키는 str() 로 강제 변환 (JSON 키는 문자열만 허용)
        - ensure_ascii=False 환경에서 한글 등 유니코드 키/값은 그대로 보존됩니다.
    """
    # ── 기본 타입 (빠른 경로) ─────────────────────────────────────
    if obj is None:
        return None

    # bool 은 int 서브클래스이므로 반드시 int 보다 먼저 처리
    if isinstance(obj, bool):
        return obj

    if isinstance(obj, int):
        return obj

    if isinstance(obj, str):
        return obj

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    # ── 컨테이너 ─────────────────────────────────────────────────
    if isinstance(obj, dict):
        # JSON 키는 문자열만 허용 -> str(k)
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]

    # ── dataclass ────────────────────────────────────────────────
    # is_dataclass(type) == True 이므로 not isinstance(obj, type) 필수
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # asdict 은 중첩 dataclass를 dict로 바꾸지만 numpy 등은 그대로 남김
        # -> 결과를 다시 to_jsonable 로 순회
        return to_jsonable(dataclasses.asdict(obj))

    # ── pydantic BaseModel ───────────────────────────────────────
    # pydantic v2: model_dump() / pydantic v1: dict()
    # __fields__ 존재 여부로 pydantic 모델 여부를 추가 확인해 false positive 방지
    try:
        if hasattr(obj, "model_dump") and hasattr(type(obj), "model_fields"):
            # pydantic v2
            return to_jsonable(obj.model_dump())
        if hasattr(obj, "dict") and hasattr(type(obj), "__fields__"):
            # pydantic v1
            return to_jsonable(obj.dict())
    except Exception:
        pass

    # ── numpy ────────────────────────────────────────────────────
    try:
        import numpy as np

        # np.bool_ 은 np.integer 서브클래스가 아니므로 먼저 처리
        if isinstance(obj, np.bool_):
            return bool(obj)

        if isinstance(obj, np.integer):
            return int(obj)

        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v

        if isinstance(obj, np.complexfloating):
            # 복소수는 JSON 직렬화 불가 -> 문자열 표현으로 degrade
            return str(obj)

        if isinstance(obj, np.ndarray):
            # tolist() 는 0-d array도 처리하며 Python 중첩 리스트로 변환
            # 단, object dtype array는 원소가 numpy 타입일 수 있으므로 재귀 필요
            return [to_jsonable(x) for x in obj.tolist()]

        if isinstance(obj, np.generic):
            # np.str_, np.bytes_, np.void 등 나머지 numpy scalar
            return to_jsonable(obj.item())

    except ImportError:
        pass

    # ── pandas ───────────────────────────────────────────────────
    try:
        import pandas as pd

        if isinstance(obj, pd.DataFrame):
            # orient='records' -> list[dict] -> 각 셀에 numpy 타입 가능 -> 재귀
            return [to_jsonable(row) for row in obj.to_dict(orient="records")]

        if isinstance(obj, pd.Series):
            return [to_jsonable(v) for v in obj.tolist()]

        # pd.NA, pd.NaT, np.nan 등을 pd.isna() 로 통일 처리
        # isna()는 array-like에 대해 TypeError를 낼 수 있으므로 try/except
        try:
            if pd.isna(obj):
                return None
        except (TypeError, ValueError):
            pass

    except ImportError:
        pass

    # ── 표준 라이브러리 ──────────────────────────────────────────
    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, date):
        return obj.isoformat()

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, Enum):
        return to_jsonable(obj.value)

    if isinstance(obj, bytes):
        # 바이너리는 base64 로 인코딩해 손실 없이 보존
        return base64.b64encode(obj).decode("ascii")

    # ── Fallback ─────────────────────────────────────────────────
    # summary 우선: __repr__ 보다 __str__ 이 더 사람이 읽기 좋음
    return str(obj)
