"""
Adapter Smoke Test

uvicorn/Celery 없이 로컬에서 바로 실행:
    python scripts/test_adapters_smoke.py

성공 시: SMOKE_OK 출력
실패 시: 상세 에러 출력 후 exit(1)

검증 목표:
  1. SimulationAdapter.run()   -- run_simulation() 은 mock 으로 우회
  2. YReportAdapter.generate() -- ComparisonReportGenerator 는 mock 으로 우회
  3. 두 경우 모두 반환값이 json.dumps 가능한 dict 임을 확인
  4. 반환 dict 에 필수 키(summary/raw/meta, structured/markdown/meta) 존재 확인

엔진 호출 방식:
  - ensure_engine_path() 로 실제 경로 주입 후 엔진 모듈을 import
  - import 된 모듈 속성을 unittest.mock.patch.object 로 대체
  - 따라서 엔진이 실제 실행되지 않아도 adapter 계약 검증 가능
"""
import asyncio
import io
import json
import sys
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd

# Windows cp949 콘솔 깨짐 방지
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


# ──────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────

def _check(label: str, passed: bool, detail: str = ""):
    if passed:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")


def _assert_json(obj: dict, label: str) -> bool:
    try:
        json.dumps(obj, ensure_ascii=False)
        return True
    except (TypeError, ValueError) as e:
        _check(f"{label} JSON-serializable", False, str(e))
        return False


def _assert_keys(obj: dict, required: list, label: str) -> bool:
    missing = [k for k in required if k not in obj]
    if missing:
        _check(f"{label} 필수 키 존재", False, f"누락: {missing}")
        return False
    return True


# ──────────────────────────────────────────────────────────────
# Mock 데이터
# ──────────────────────────────────────────────────────────────

# 시뮬레이션 결과 DataFrame (numpy 타입 포함 - _sanitize 검증용)
MOCK_DF = pd.DataFrame([
    {
        "timestamp": "2026-02-22 07:00",
        "agent_id": np.int64(1),
        "persona_id": "P001",
        "generation": "MZ",
        "gender_composition": "남성",
        "segment": "상주",
        "weekday": "월",
        "time_slot": "아침",
        "agent_lat": np.float64(37.498),
        "agent_lng": np.float64(127.027),
        "nearby_store_count": np.int64(10),
        "decision": "visit",
        "visited_store": "돼지야",
        "visited_category": "고기/구이",
        "rating": np.float64(4.5),
        "reason": "분위기가 좋아서",
        "comment": "정말 맛있어요",
    },
    {
        "timestamp": "2026-02-22 12:00",
        "agent_id": np.int64(2),
        "persona_id": "P002",
        "generation": "X",
        "gender_composition": "여성",
        "segment": "유동",
        "weekday": "월",
        "time_slot": "점심",
        "agent_lat": np.float64(37.499),
        "agent_lng": np.float64(127.028),
        "nearby_store_count": np.int64(5),
        "decision": "stay_home",
        "visited_store": None,
        "visited_category": None,
        "rating": None,
        "reason": "오늘은 집에 있을게요",
        "comment": None,
    },
])


def _make_mock_sim_output() -> dict:
    """run_full_pipeline() 출력 형식의 mock simulation_output"""
    return {
        "target_store": "돼지야",
        "before": {
            "output_dir": "/mock/before",
            "visit_count": 50,
            "avg_rating": 4.2,
        },
        "after": {
            "output_dir": "/mock/after",
            "visit_count": 65,
            "avg_rating": 4.5,
        },
    }


def _make_mock_report_path() -> MagicMock:
    """ComparisonReportGenerator.generate_all() 반환값 mock.
    MagicMock 은 기본적으로 truthy 이므로 __bool__ 별도 설정 불필요.
    str() 은 기본 MagicMock 표현을 그대로 사용 (JSON-serializable 문자열).
    """
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = "# 비교 분석 보고서\n\n전략 후 방문 수 30% 증가"
    return mock_path


# ──────────────────────────────────────────────────────────────
# 테스트 1: SimulationAdapter.run()
# ──────────────────────────────────────────────────────────────

async def test_simulation_adapter():
    print("\n[Test 1] SimulationAdapter.run()")

    # 1-A. engine path 설정 및 simulation 모듈 pre-import
    from app.engine.path_setup import ensure_engine_path
    ensure_engine_path()

    import scripts.run_generative_simulation as _sim_mod

    # 1-B. run_simulation 을 AsyncMock 으로 교체
    mock_run = AsyncMock(return_value=MOCK_DF)
    failed = 0

    with patch.object(_sim_mod, "run_simulation", mock_run):
        from app.adapters.simulation_adapter import SimulationAdapter

        adapter = SimulationAdapter()
        mock_agents = [MagicMock(), MagicMock(), MagicMock()]
        mock_global_store = MagicMock()
        mock_settings = MagicMock()

        try:
            result = await adapter.run(
                agents=mock_agents,
                global_store=mock_global_store,
                settings=mock_settings,
                days=1,
                max_concurrent_llm_calls=1,
            )
        except Exception as e:
            _check("SimulationAdapter.run() 호출", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
            return False

    # 1-C. 반환값 검증
    ok = _assert_json(result, "SimulationAdapter")
    if not ok:
        failed += 1

    ok = _assert_keys(result, ["summary", "raw", "meta"], "SimulationAdapter")
    if not ok:
        failed += 1
    else:
        _check("SimulationAdapter 필수 키 존재 (summary/raw/meta)", True)

    ok = _assert_keys(result["summary"],
                      ["total_events", "visit_count", "visit_rate_pct", "avg_rating", "top10_stores"],
                      "summary")
    if ok:
        _check("summary 세부 키 존재", True)
    else:
        failed += 1

    ok = _assert_keys(result["meta"],
                      ["days", "agent_count", "max_concurrent_llm_calls", "elapsed_sec"],
                      "meta")
    if ok:
        _check("meta 세부 키 존재", True)
    else:
        failed += 1

    _check("SimulationAdapter JSON-serializable", True)

    # 1-D. 한글 보존 확인
    serialized = json.dumps(result, ensure_ascii=False)
    has_korean = "돼지야" in serialized
    _check("한글 보존 (ensure_ascii=False)", has_korean,
           "top10_stores 에 한글 매장명 없음" if not has_korean else "")
    if not has_korean:
        failed += 1

    # 1-E. NaN -> None 변환 확인
    records = result["raw"]["records"]
    has_nan = any(
        isinstance(v, float) and (v != v)  # NaN check
        for row in records for v in row.values()
    )
    _check("NaN -> None 변환 (raw.records)", not has_nan,
           "NaN 이 raw.records 에 남아 있음" if has_nan else "")
    if has_nan:
        failed += 1

    print(f"  mock call count: {mock_run.call_count}")
    return failed == 0


# ──────────────────────────────────────────────────────────────
# 테스트 2: YReportAdapter.generate()
# ──────────────────────────────────────────────────────────────

async def test_y_report_adapter():
    print("\n[Test 2] YReportAdapter.generate()")

    # 2-A. engine path 설정 및 sim_to_y 모듈 pre-import
    from app.engine.path_setup import ensure_engine_path
    ensure_engine_path()

    import sim_to_y as _sim_to_y_mod

    # 2-B. ComparisonReportGenerator 를 MagicMock 으로 교체
    mock_report_path = _make_mock_report_path()
    MockGenClass = MagicMock()
    MockGenClass.return_value.generate_all.return_value = mock_report_path
    MockGenClass.return_value.metrics_summary = {
        "visit_change_pct": 30.0,
        "avg_rating_before": 4.2,
        "avg_rating_after": 4.5,
        "retention_rate": 0.12,
        "top_keyword": "가성비",
    }

    failed = 0
    simulation_output = _make_mock_sim_output()

    with patch.object(_sim_to_y_mod, "ComparisonReportGenerator", MockGenClass):
        from app.adapters.y_report_adapter import YReportAdapter

        adapter = YReportAdapter()
        try:
            result = await adapter.generate(
                simulation_output=simulation_output,
                x_report={"strategy_ids": ["S1_A", "S2_B"], "store": "돼지야"},
                options={},  # before/after_dir 은 simulation_output 에서 자동 추출
            )
        except Exception as e:
            _check("YReportAdapter.generate() 호출", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
            return False

    # 2-C. 반환값 검증
    ok = _assert_json(result, "YReportAdapter")
    if not ok:
        failed += 1

    ok = _assert_keys(result, ["structured", "markdown", "meta"], "YReportAdapter")
    if not ok:
        failed += 1
    else:
        _check("YReportAdapter 필수 키 존재 (structured/markdown/meta)", True)

    ok = _assert_keys(result["meta"],
                      ["target_store", "before_dir", "after_dir", "report_path"],
                      "meta")
    if ok:
        _check("meta 세부 키 존재", True)
    else:
        failed += 1

    # 2-D. markdown 내용 확인
    has_markdown = bool(result.get("markdown", ""))
    _check("markdown 비어있지 않음", has_markdown,
           "markdown 이 빈 문자열" if not has_markdown else "")
    if not has_markdown:
        failed += 1

    # 2-E. before/after_dir 이 simulation_output 에서 자동 추출됐는지 확인
    meta = result["meta"]
    correct_before = meta.get("before_dir") == simulation_output["before"]["output_dir"]
    correct_after = meta.get("after_dir") == simulation_output["after"]["output_dir"]
    _check("before/after_dir 자동 추출 (simulation_output)", correct_before and correct_after,
           f"before_dir={meta.get('before_dir')}, after_dir={meta.get('after_dir')}")
    if not (correct_before and correct_after):
        failed += 1

    _check("YReportAdapter JSON-serializable", True)

    print(f"  MockGen call count: {MockGenClass.call_count}")
    return failed == 0


# ──────────────────────────────────────────────────────────────
# 테스트 3: ValueError (before_dir 누락)
# ──────────────────────────────────────────────────────────────

async def test_y_report_missing_dir():
    print("\n[Test 3] YReportAdapter.generate() -- before_dir 누락 -> ValueError")

    from app.adapters.y_report_adapter import YReportAdapter

    adapter = YReportAdapter()
    try:
        await adapter.generate(
            simulation_output={"target_store": "없는가게", "before": {}, "after": {}},
            x_report={},
            options={},
        )
        _check("ValueError 발생", False, "예외가 발생하지 않음")
        return False
    except ValueError as e:
        _check(f"ValueError 발생 (예상): {str(e)[:60]}", True)
        return True
    except Exception as e:
        _check("ValueError 발생", False, f"예상 외 예외: {type(e).__name__}: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

async def main():
    print("=" * 56)
    print("  Adapter Smoke Test")
    print("=" * 56)

    results = []

    # Test 1: SimulationAdapter
    try:
        ok = await test_simulation_adapter()
        results.append(ok)
        status = "PASS" if ok else "FAIL"
        print(f"  -> Test 1: {status}")
    except Exception as e:
        print(f"  -> Test 1: ERROR - {type(e).__name__}: {e}")
        traceback.print_exc()
        results.append(False)

    # Test 2: YReportAdapter
    try:
        ok = await test_y_report_adapter()
        results.append(ok)
        status = "PASS" if ok else "FAIL"
        print(f"  -> Test 2: {status}")
    except Exception as e:
        print(f"  -> Test 2: ERROR - {type(e).__name__}: {e}")
        traceback.print_exc()
        results.append(False)

    # Test 3: ValueError
    try:
        ok = await test_y_report_missing_dir()
        results.append(ok)
        status = "PASS" if ok else "FAIL"
        print(f"  -> Test 3: {status}")
    except Exception as e:
        print(f"  -> Test 3: ERROR - {type(e).__name__}: {e}")
        traceback.print_exc()
        results.append(False)

    # 결과
    print("=" * 56)
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} PASS")

    if passed == total:
        print("SMOKE_OK")
        sys.exit(0)
    else:
        print(f"FAILED ({total - passed} 테스트 실패)")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
