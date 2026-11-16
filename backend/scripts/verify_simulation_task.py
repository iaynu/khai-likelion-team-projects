"""
Simulation Task 통합 검증 스크립트

uvicorn/Celery worker 없이 로컬에서 바로 실행:
    python scripts/verify_simulation_task.py

Mock 모드: SimulationService.run()을 패치하여 실제 시뮬레이션 엔진 없이
Task의 TX 전환 흐름 및 에러 처리를 검증한다.

케이스 1: 성공 — 정상 output 반환 → TX1→TX2, COMPLETED 전환 확인
케이스 2: 타임아웃 — asyncio.TimeoutError → [BACKEND_TIMEOUT] 에러코드, TX3, FAILED 확인
케이스 3: 알 수 없는 오류 — ValueError → [UNKNOWN_ERROR] 에러코드, TX3, FAILED 확인

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

async def _create_sim_and_job(store_source_id: str):
    """테스트용 Simulation + Job 레코드 생성"""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo
    from app.models.job import JobType

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id=store_source_id,
                x_report_id=None,
                selected_strategy_ids=["S1_A"],
                days=1,
                seed=42,
                settings_json={"agent_count": 5},
            )
            job = await JobRepo(session).create(
                job_type=JobType.SIMULATION,
                store_source_id=store_source_id,
            )
            return sim.id, job.id


async def _get_sim_and_job(sim_id: int, job_id: int):
    """DB에서 Simulation, Job 상태 조회"""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        sim = await SimulationRepo(session).get_by_id(sim_id)
        job = await JobRepo(session).get_by_id(job_id)
        return sim, job


# ──────────────────────────────────────────────────────────────
# Mock 출력 데이터
# ──────────────────────────────────────────────────────────────

MOCK_SERVICE_RESULT = {
    "output": {
        "target_store": "돼지야",
        "before": {
            "visit_count": 42,
            "avg_rating": 4.2,
            "target_visit_count": 10,
            "top10_stores": [{"store": "돼지야", "count": 10}],
            "output_dir": "/mock/before",
        },
        "after": None,
    },
    "meta": {
        "engine": "run_full_pipeline",
        "run_id": "mock-run-id-001",
        "timing_ms": 123,
        "agent_count": 5,
        "days": 1,
        "max_concurrent_llm_calls": 5,
        "llm_tokens": None,
    },
}


# ──────────────────────────────────────────────────────────────
# 케이스 1: 성공 (Mock)
# ──────────────────────────────────────────────────────────────

async def case1_success_mock() -> bool:
    """TX1 → mock 엔진 → TX2 흐름 검증. COMPLETED 상태 확인."""
    header("케이스 1: 성공 Mock (TX1 → TX2)")

    from tasks.simulation_task import _tx1_processing, _tx2_completed
    from app.services.simulation_service import SimulationService

    sim_id, job_id = await _create_sim_and_job("__mock_success__")
    print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

    # TX1
    await _tx1_processing(job_id)
    _, job = await _get_sim_and_job(sim_id, job_id)
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status}")

    # SimulationService.run() Mock
    with patch.object(
        SimulationService, "run", AsyncMock(return_value=MOCK_SERVICE_RESULT)
    ):
        result = await SimulationService().run(
            store_source_id="__mock_success__",
            selected_strategy_ids=["S1_A"],
            days=1,
            seed=42,
            agent_count=5,
        )

    # TX2
    output_json = {**result["output"], "meta": result["meta"]}
    await _tx2_completed(job_id, sim_id, output_json)

    sim, job = await _get_sim_and_job(sim_id, job_id)
    assert sim.status == "COMPLETED", f"TX2 후 sim.status 불일치: {sim.status}"
    assert job.status.value == "completed", f"TX2 후 job.status 불일치: {job.status}"
    assert job.result_id == sim_id, "job.result_id 불일치"

    # output_json 검증
    assert sim.output_json["target_store"] == "돼지야"
    assert sim.output_json["meta"]["engine"] == "run_full_pipeline"
    assert sim.output_json["meta"]["run_id"] == "mock-run-id-001"
    serialized = json.dumps(sim.output_json, ensure_ascii=False)
    assert "돼지야" in serialized
    print(f"  [OK] TX2: sim.status={sim.status}, job.status={job.status}")
    print(f"  [OK] output_json JSON-serializable ({len(serialized)} chars)")
    print(f"  [OK] meta.run_id = {sim.output_json['meta']['run_id']}")
    print("  [PASS] 케이스 1 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 2: 타임아웃 → [BACKEND_TIMEOUT]
# ──────────────────────────────────────────────────────────────

async def case2_timeout_mock() -> bool:
    """asyncio.TimeoutError 발생 시 [BACKEND_TIMEOUT] 에러코드 확인."""
    header("케이스 2: 타임아웃 Mock → [BACKEND_TIMEOUT]")

    from tasks.simulation_task import _tx1_processing, _tx3_failed
    from app.services.simulation_service import SimulationService

    sim_id, job_id = await _create_sim_and_job("__mock_timeout__")
    print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

    await _tx1_processing(job_id)

    # run_full_pipeline이 TimeoutError를 발생시키도록 패치
    with patch(
        "app.adapters.simulation_adapter.run_full_pipeline",
        AsyncMock(side_effect=asyncio.TimeoutError("LLM 응답 초과")),
    ):
        try:
            await SimulationService().run(
                store_source_id="__mock_timeout__",
                selected_strategy_ids=[],
                days=1,
                seed=42,
                agent_count=5,
            )
            print("  [FAIL] 예외가 발생하지 않음 (RuntimeError 예상)")
            return False
        except RuntimeError as e:
            error_msg = str(e)
            print(f"  [OK] RuntimeError: {error_msg[:100]}")
            assert "[BACKEND_TIMEOUT]" in error_msg, \
                f"에러코드 BACKEND_TIMEOUT 없음: {error_msg}"

    # TX3
    full_error = f"{error_msg[:550]} | Context: store=__mock_timeout__, sim_id={sim_id}"
    await _tx3_failed(job_id, sim_id, full_error)

    sim, job = await _get_sim_and_job(sim_id, job_id)
    assert sim.status == "FAILED", f"TX3 후 sim.status 불일치: {sim.status}"
    assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
    assert "[BACKEND_TIMEOUT]" in sim.error_message, \
        f"error_message에 [BACKEND_TIMEOUT] 없음: {sim.error_message[:100]}"
    print(f"  [OK] TX3: sim.status={sim.status}, job.status={job.status}")
    print(f"  [OK] error_message: {sim.error_message[:80]}")
    print("  [PASS] 케이스 2 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 3: 알 수 없는 오류 → [UNKNOWN_ERROR]
# ──────────────────────────────────────────────────────────────

async def case3_unknown_error_mock() -> bool:
    """ValueError 발생 시 [UNKNOWN_ERROR] 에러코드 확인."""
    header("케이스 3: 알 수 없는 오류 Mock → [UNKNOWN_ERROR]")

    from tasks.simulation_task import _tx1_processing, _tx3_failed, _format_error
    from app.services.simulation_service import SimulationService

    sim_id, job_id = await _create_sim_and_job("__mock_unknown__")
    print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

    await _tx1_processing(job_id)

    # run_full_pipeline이 ValueError를 발생시키도록 패치
    with patch(
        "app.adapters.simulation_adapter.run_full_pipeline",
        AsyncMock(side_effect=ValueError("매장 데이터 없음: __mock_unknown__")),
    ):
        try:
            await SimulationService().run(
                store_source_id="__mock_unknown__",
                selected_strategy_ids=[],
                days=1,
                seed=42,
                agent_count=5,
            )
            print("  [FAIL] 예외가 발생하지 않음 (RuntimeError 예상)")
            return False
        except RuntimeError as e:
            error_msg = str(e)
            print(f"  [OK] RuntimeError: {error_msg[:100]}")
            assert "[UNKNOWN_ERROR]" in error_msg, \
                f"에러코드 UNKNOWN_ERROR 없음: {error_msg}"
            assert "ValueError" in error_msg, \
                f"원본 예외 타입 없음: {error_msg}"

    # Task에서 하는 방식대로 에러 포맷 후 TX3
    context = f"store=__mock_unknown__, sim_id={sim_id}, job_id={job_id}"
    full_error = f"{error_msg[:550]} | Context: {context}"
    await _tx3_failed(job_id, sim_id, full_error)

    sim, job = await _get_sim_and_job(sim_id, job_id)
    assert sim.status == "FAILED", f"TX3 후 sim.status 불일치: {sim.status}"
    assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
    assert "[UNKNOWN_ERROR]" in sim.error_message, \
        f"error_message에 [UNKNOWN_ERROR] 없음: {sim.error_message[:100]}"
    print(f"  [OK] TX3: sim.status={sim.status}, job.status={job.status}")
    print(f"  [OK] error_message: {sim.error_message[:80]}")
    print("  [PASS] 케이스 3 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main():
    header("Simulation Task 통합 검증 (Mock 모드)")
    print("  uvicorn/Celery 없이 TX 전환 + 에러코드 검증")

    results = []
    for coro, name in [
        (case1_success_mock, "케이스 1"),
        (case2_timeout_mock, "케이스 2"),
        (case3_unknown_error_mock, "케이스 3"),
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
        print("  SIM_TASK_OK")
        sys.exit(0)
    else:
        print("  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
