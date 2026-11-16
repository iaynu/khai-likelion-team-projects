"""
YReport Task 통합 검증 스크립트

uvicorn/Celery worker 없이 로컬에서 바로 실행:
    python scripts/verify_y_report_task.py

Mock 모드: YReportAdapter.generate()를 패치하여 실제 시뮬레이션 출력 파일 없이
Task의 TX 전환 흐름 및 에러 처리를 검증한다.

케이스 1: 성공 — mock 어댑터 결과 반환 → TX1→TX2, COMPLETED, output_json·markdown 확인
케이스 2: 입력 오류 — ValueError → [INVALID_INPUT] 에러코드, TX3, FAILED 확인
케이스 3: 파일 없음 — FileNotFoundError → [MISSING_DATA] 에러코드, TX3, FAILED 확인

완료 조건: 세 케이스 모두 PASS
"""
import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


def header(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────
# DB 헬퍼
# ──────────────────────────────────────────────────────────────

async def _create_completed_sim(store_source_id: str, before_dir: str, after_dir: str) -> int:
    """COMPLETED Simulation 레코드 생성 (mock before/after dirs 포함)"""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo

    output_json = {
        "target_store": store_source_id,
        "before": {"output_dir": before_dir, "visit_count": 100},
        "after": {"output_dir": after_dir, "visit_count": 120},
    }

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_completed(
                store_source_id=store_source_id,
                x_report_id=None,
                selected_strategy_ids=["S1_A"],
                days=1,
                seed=42,
                output_json=output_json,
            )
            return sim.id


async def _create_yr_and_job(store_source_id: str, sim_id: int):
    """테스트용 YReport + Job 레코드 생성"""
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo
    from app.models.job import JobType

    async with get_async_session() as session:
        async with session.begin():
            yr = await YReportRepo(session).create_pending(
                store_source_id=store_source_id,
                simulation_id=sim_id,
            )
            job = await JobRepo(session).create(
                job_type=JobType.Y_REPORT,
                store_source_id=store_source_id,
            )
            return yr.id, job.id


async def _get_yr_and_job(yr_id: int, job_id: int):
    """DB에서 YReport, Job 상태 조회"""
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        yr = await YReportRepo(session).get_by_id(yr_id)
        job = await JobRepo(session).get_by_id(job_id)
        return yr, job


# ──────────────────────────────────────────────────────────────
# Mock 어댑터 반환값
# ──────────────────────────────────────────────────────────────

MOCK_ADAPTER_RESULT = {
    "structured": {
        "total_visits_before": 100,
        "total_visits_after": 120,
        "visit_change_pct": 20.0,
        "avg_rating_before": 3.5,
        "avg_rating_after": 3.8,
        "retention_rate": 0.6,
        "korean_note": "방문율 증가",
    },
    "markdown": (
        "# 전략 전/후 비교 분석 보고서\n\n"
        "## 솔루션 완성도 진단\n\n"
        "방문율이 20% 증가하였습니다.\n"
    ),
    "meta": {
        "target_store": "돼지야",
        "before_dir": "/mock/before",
        "after_dir": "/mock/after",
        "report_path": "/mock/after/y_report/Comparison_Report.md",
        "x_report_keys": [],
    },
}


# ──────────────────────────────────────────────────────────────
# 케이스 1: 성공 (Mock)
# ──────────────────────────────────────────────────────────────

async def case1_success_mock() -> bool:
    """TX1 → mock 어댑터 → TX2 흐름 검증. COMPLETED 및 output_json·markdown 확인."""
    header("케이스 1: 성공 Mock (TX1 → TX2)")

    from app.adapters.y_report_adapter import YReportAdapter
    from tasks.y_report_task import _tx1_processing, _tx2_completed
    from app.services.y_report_service import YReportService

    sim_id = await _create_completed_sim("돼지야", "/mock/before", "/mock/after")
    yr_id, job_id = await _create_yr_and_job("돼지야", sim_id)
    print(f"  [OK] Simulation {sim_id}, YReport {yr_id}, Job {job_id} 생성")

    # TX1
    await _tx1_processing(job_id)
    _, job = await _get_yr_and_job(yr_id, job_id)
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status}")

    # YReportAdapter.generate() mock
    with patch.object(
        YReportAdapter, "generate", AsyncMock(return_value=MOCK_ADAPTER_RESULT)
    ):
        result = await YReportService().run(
            simulation_id=sim_id,
            x_report_id=None,
            options={},
        )

    print(f"  [OK] Service.run(): run_id={result['meta']['run_id'][:8]}...")

    # TX2
    output_json = {
        "structured": result["output"].get("structured", {}),
        "adapter_meta": result["output"].get("meta", {}),
        "service_meta": result["meta"],
    }
    markdown = result["output"].get("markdown", "")
    await _tx2_completed(job_id, yr_id, output_json, markdown)

    yr, job = await _get_yr_and_job(yr_id, job_id)
    assert yr.status == "COMPLETED", f"TX2 후 yr.status 불일치: {yr.status}"
    assert job.status.value == "completed", f"TX2 후 job.status 불일치: {job.status}"
    assert job.result_id == yr_id, "job.result_id 불일치"

    # output_json 검증
    assert yr.output_json["structured"]["visit_change_pct"] == 20.0
    assert yr.output_json["structured"]["korean_note"] == "방문율 증가"
    assert yr.output_json["service_meta"]["engine"] == "YReportAdapter"
    assert yr.output_json["adapter_meta"]["target_store"] == "돼지야"

    # markdown 검증
    assert "방문율" in yr.markdown
    assert yr.markdown.startswith("# 전략 전/후")

    # JSON 직렬화 검증
    serialized = json.dumps(yr.output_json, ensure_ascii=False)
    assert "방문율" in serialized
    print(f"  [OK] TX2: yr.status={yr.status}, job.status={job.status}")
    print(f"  [OK] output_json JSON-serializable ({len(serialized)} chars)")
    print(f"  [OK] markdown: {len(yr.markdown)} chars, starts='{yr.markdown[:20]}'")
    print("  [PASS] 케이스 1 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 2: 입력 오류 → [INVALID_INPUT]
# ──────────────────────────────────────────────────────────────

async def case2_invalid_input_mock() -> bool:
    """어댑터 ValueError → [INVALID_INPUT] 에러코드 확인."""
    header("케이스 2: 입력 오류 Mock → [INVALID_INPUT]")

    from app.adapters.y_report_adapter import YReportAdapter
    from tasks.y_report_task import _tx1_processing, _tx3_failed
    from app.services.y_report_service import YReportService

    sim_id = await _create_completed_sim("__mock_invalid__", "/mock/before", "/mock/after")
    yr_id, job_id = await _create_yr_and_job("__mock_invalid__", sim_id)
    print(f"  [OK] Simulation {sim_id}, YReport {yr_id}, Job {job_id} 생성")

    await _tx1_processing(job_id)

    # 어댑터가 ValueError 발생 → 서비스가 [INVALID_INPUT]으로 래핑
    with patch.object(
        YReportAdapter, "generate",
        AsyncMock(side_effect=ValueError("before_dir 또는 after_dir 가 없습니다")),
    ):
        try:
            await YReportService().run(
                simulation_id=sim_id,
                x_report_id=None,
                options={},
            )
            print("  [FAIL] 예외가 발생하지 않음 (RuntimeError 예상)")
            return False
        except RuntimeError as e:
            error_msg = str(e)
            print(f"  [OK] RuntimeError: {error_msg[:100]}")
            assert "[INVALID_INPUT]" in error_msg, \
                f"에러코드 INVALID_INPUT 없음: {error_msg}"

    # TX3
    full_error = f"{error_msg[:550]} | Context: y_report_id={yr_id}"
    await _tx3_failed(job_id, yr_id, full_error)

    yr, job = await _get_yr_and_job(yr_id, job_id)
    assert yr.status == "FAILED", f"TX3 후 yr.status 불일치: {yr.status}"
    assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
    assert "[INVALID_INPUT]" in yr.error_message, \
        f"error_message에 [INVALID_INPUT] 없음: {yr.error_message[:100]}"
    print(f"  [OK] TX3: yr.status={yr.status}, job.status={job.status}")
    print(f"  [OK] error_message: {yr.error_message[:80]}")
    print("  [PASS] 케이스 2 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 3: 파일 없음 → [MISSING_DATA]
# ──────────────────────────────────────────────────────────────

async def case3_missing_data_mock() -> bool:
    """어댑터 FileNotFoundError → [MISSING_DATA] 에러코드 확인."""
    header("케이스 3: 파일 없음 Mock → [MISSING_DATA]")

    from app.adapters.y_report_adapter import YReportAdapter
    from tasks.y_report_task import _tx1_processing, _tx3_failed
    from app.services.y_report_service import YReportService

    sim_id = await _create_completed_sim("__mock_missing__", "/mock/before", "/mock/after")
    yr_id, job_id = await _create_yr_and_job("__mock_missing__", sim_id)
    print(f"  [OK] Simulation {sim_id}, YReport {yr_id}, Job {job_id} 생성")

    await _tx1_processing(job_id)

    # 어댑터가 FileNotFoundError 발생 → 서비스가 [MISSING_DATA]로 래핑
    with patch.object(
        YReportAdapter, "generate",
        AsyncMock(side_effect=FileNotFoundError("/mock/before not found")),
    ):
        try:
            await YReportService().run(
                simulation_id=sim_id,
                x_report_id=None,
                options={},
            )
            print("  [FAIL] 예외가 발생하지 않음 (RuntimeError 예상)")
            return False
        except RuntimeError as e:
            error_msg = str(e)
            print(f"  [OK] RuntimeError: {error_msg[:100]}")
            assert "[MISSING_DATA]" in error_msg, \
                f"에러코드 MISSING_DATA 없음: {error_msg}"

    # TX3
    full_error = f"{error_msg[:550]} | Context: y_report_id={yr_id}"
    await _tx3_failed(job_id, yr_id, full_error)

    yr, job = await _get_yr_and_job(yr_id, job_id)
    assert yr.status == "FAILED", f"TX3 후 yr.status 불일치: {yr.status}"
    assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
    assert "[MISSING_DATA]" in yr.error_message, \
        f"error_message에 [MISSING_DATA] 없음: {yr.error_message[:100]}"
    print(f"  [OK] TX3: yr.status={yr.status}, job.status={job.status}")
    print(f"  [OK] error_message: {yr.error_message[:80]}")
    print("  [PASS] 케이스 3 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main():
    header("YReport Task 통합 검증 (Mock 모드)")
    print("  uvicorn/Celery 없이 TX 전환 + 에러코드 검증")

    results = []
    for coro, name in [
        (case1_success_mock, "케이스 1"),
        (case2_invalid_input_mock, "케이스 2"),
        (case3_missing_data_mock, "케이스 3"),
    ]:
        try:
            results.append(await coro())
        except Exception as e:
            import traceback
            print(f"  [FAIL] {name} 예외: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append(False)

    header("최종 결과")
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} PASS")

    if passed == total:
        print("  Y_REPORT_TASK_OK")
        sys.exit(0)
    else:
        print("  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
