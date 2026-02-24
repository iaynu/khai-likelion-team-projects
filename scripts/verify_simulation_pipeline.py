"""
Simulation 파이프라인 통합 검증 스크립트

# ── 실행 커맨드 ────────────────────────────────────────────────
#
#   [mock 모드]  — 실제 LLM/엔진 없이 3-TX 전환 + DB 저장 검증 (default)
#   python scripts/verify_simulation_pipeline.py
#   LOG_LEVEL=DEBUG SQL_ECHO=1 python scripts/verify_simulation_pipeline.py
#
#   [real 모드]  — 실제 SimulationService.run() 호출 + API 응답 검증
#   ENGINE_MODE=real STORE_ID=돼지야 python scripts/verify_simulation_pipeline.py
#   ENGINE_MODE=real STORE_ID=돼지야 SIM_DAYS=1 MAX_CONCURRENT_LLM_CALLS=5 python scripts/verify_simulation_pipeline.py
#   (STORE_ID 생략 시 DB에서 가장 최근 XReport 매장 자동 사용)
#
# ── 실행 결과 예시 ─────────────────────────────────────────────
#
#   [mock 모드]
#   ============================================================
#     Simulation 파이프라인 통합 검증 (Mock 모드)
#   ============================================================
#     ENGINE_MODE=mock  LOG_LEVEL=WARNING  SQL_ECHO=False
#     [OK] Simulation 3, Job 3 생성
#     [OK] TX1: job.status = processing
#     [OK] TX2: sim.status=COMPLETED, job.status=completed
#     [OK] output_json 저장 확인 (engine=run_full_pipeline)
#     [OK] output_json JSON-serializable (312 chars)
#     [PASS] 케이스 1 성공
#     [OK] TX3: sim.status=FAILED, job.status=failed
#     [OK] error_message prefix: [BACKEND_TIMEOUT] 시뮬레이션 시간 초과 mock | ...
#     [PASS] 케이스 2 성공
#     2/2 PASS  SIM_PIPELINE_OK
#
#   [real 모드]
#   ============================================================
#     Simulation 파이프라인 통합 검증 (Real Engine 모드)
#   ============================================================
#     ENGINE_MODE=real  STORE_ID=돼지야  SIM_DAYS=1  agent_count=5
#     [OK] Simulation 5, Job 5 생성
#     [OK] TX1: job.status = processing
#     [..] 엔진 실행 중 (SIM_DAYS=1, agent_count=5)...
#     [OK] TX2: COMPLETED — output_json 1523 chars (JSON-serializable)
#     [OK] GET /simulations/5 → status=COMPLETED
#     [OK] GET /jobs/5 → status=completed, result_id=5
#     [PASS] Real Engine 케이스 성공 (결과: completed)
#     1/1 PASS  SIM_PIPELINE_OK

uvicorn/Celery worker 없이 로컬에서 바로 실행.

Mock 모드:
  SimulationService.run()을 mock하여 3-TX 전환 흐름 및 DB 저장을 검증한다.
  케이스 1: TX1(PROCESSING) → TX2(COMPLETED), output_json 저장 확인
  케이스 2: TX1(PROCESSING) → TX3(FAILED), [BACKEND_TIMEOUT] prefix 확인

Real 모드:
  실제 SimulationService.run()을 호출하여 엔진 실행 + DB 저장을 검증한다.
  성공/실패 모두 허용 — 3-TX 패턴 일관성 + JSON 직렬화 가능성을 검증한다.
  완료 후 in-process AsyncClient로 GET /simulations/{id}, GET /jobs/{id} 응답을 검증한다.

세션 분리 원칙:
  Session A: _tx* 헬퍼 내부에서 독립 생성 (Task 실행 세션)
  Session B: _verify() / _cleanup() 전용 세션 (별도 context manager)

cleanup: finally 블록에서 항상 수행 (성공/실패 무관)
"""
import asyncio
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import AsyncMock, patch

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


# ── 환경 변수 ───────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING").upper()
SQL_ECHO  = os.environ.get("SQL_ECHO", "0") == "1"

# ENGINE_MODE: mock(default) | real
ENGINE_MODE = os.environ.get("ENGINE_MODE", "mock").lower()

# Real 모드 파라미터
STORE_ID                 = os.environ.get("STORE_ID", "")
X_REPORT_ID_STR          = os.environ.get("X_REPORT_ID", "")
SIM_DAYS                 = int(os.environ.get("SIM_DAYS", "1"))
MAX_CONCURRENT_LLM_CALLS = int(os.environ.get("MAX_CONCURRENT_LLM_CALLS", "5"))
try:
    SIM_AGENT_COUNT = json.loads(os.environ.get("SIM_SETTINGS_JSON", "{}")).get("agent_count", 5)
except json.JSONDecodeError:
    SIM_AGENT_COUNT = 5

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.core.config import settings   # noqa: E402
settings.SQL_ECHO = SQL_ECHO
from app.db.base import reset_engine   # noqa: E402
reset_engine()


# ──────────────────────────────────────────────────────────────
# 출력 헬퍼
# ──────────────────────────────────────────────────────────────

def header(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────
# Mock 데이터 — SimulationService.run() 반환값 형식과 일치
# ──────────────────────────────────────────────────────────────

MOCK_SERVICE_RESULT = {
    "output": {
        "target_store": "돼지야_pipeline",
        "before": {"output_dir": "/mock/before", "visit_count": 100},
        "after":  {"output_dir": "/mock/after",  "visit_count": 120},
    },
    "meta": {
        "engine": "run_full_pipeline",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "timing_ms": 500,
        "agent_count": 5,
        "days": 1,
        "max_concurrent_llm_calls": 5,
        "llm_tokens": None,
    },
}


# ──────────────────────────────────────────────────────────────
# DB 헬퍼 (Session A / Session B 명시)
# ──────────────────────────────────────────────────────────────

async def _create_sim_and_job(
    store_id: str,
    x_report_id: Optional[int] = None,
    days: int = 1,
    agent_count: int = 5,
) -> Tuple[int, int]:
    """[Session A-init] PENDING Simulation + Job 레코드를 함께 생성한다."""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo
    from app.models.job import JobType

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id=store_id,
                x_report_id=x_report_id,
                selected_strategy_ids=[],
                days=days,
                seed=42,
                input_json={"verify": True, "engine_mode": ENGINE_MODE},
                settings_json={"agent_count": agent_count},
            )
            job = await JobRepo(session).create(
                job_type=JobType.SIMULATION,
                store_source_id=store_id,
            )
    return sim.id, job.id


async def _verify(sim_id: int, job_id: int) -> Tuple:
    """[Session B] Simulation + Job을 독립 세션으로 조회한다."""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        sim = await SimulationRepo(session).get_by_id(sim_id)
        job = await JobRepo(session).get_by_id(job_id)
    return sim, job


async def _cleanup(sim_id: int, job_id: int) -> None:
    """[Session B] 생성한 Simulation + Job 레코드를 삭제한다."""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).get_by_id(sim_id)
            if sim:
                await session.delete(sim)
            job = await JobRepo(session).get_by_id(job_id)
            if job:
                await session.delete(job)
    print(f"  [OK] cleanup: Simulation {sim_id}, Job {job_id} 삭제")


# ──────────────────────────────────────────────────────────────
# Real 모드 전용 헬퍼
# ──────────────────────────────────────────────────────────────

async def _resolve_store_and_xreport() -> Tuple[str, Optional[int]]:
    """store_id 와 x_report_id 를 결정한다.

    우선순위:
    1) STORE_ID 환경변수 → 그대로 사용 (X_REPORT_ID도 함께 사용 가능)
    2) 없으면 DB에서 가장 최근 XReport 1개를 찾아
       XReport.store_id → Store.store_source_id 를 역추적한다.
    """
    x_report_id_int: Optional[int] = int(X_REPORT_ID_STR) if X_REPORT_ID_STR else None

    if STORE_ID:
        return STORE_ID, x_report_id_int

    # STORE_ID 없음 → DB에서 자동 조회
    from sqlalchemy import select, desc
    from app.db.session import get_async_session
    from app.models.x_report import XReport
    from app.models.store import Store

    async with get_async_session() as session:
        stmt = (
            select(XReport, Store.store_source_id)
            .join(Store, Store.id == XReport.store_id)
            .order_by(desc(XReport.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        row = result.one_or_none()

    if not row:
        raise ValueError(
            "DB에서 XReport를 찾을 수 없습니다.\n"
            "STORE_ID 환경변수를 직접 설정하거나, /admin/ingest + /x-reports를 먼저 실행하세요.\n"
            "예: ENGINE_MODE=real STORE_ID=돼지야 python scripts/verify_simulation_pipeline.py"
        )

    xr, store_source_id = row
    print(f"  [AUTO] DB 최근 XReport 사용: id={xr.id}, store={store_source_id}")
    return store_source_id, xr.id


async def _verify_api(sim_id: int, job_id: int, final_status: str) -> None:
    """[in-process] GET /simulations/{id} + GET /jobs/{id} 응답을 검증한다.

    서버 프로세스 없이 httpx.AsyncClient + ASGITransport로 in-process 호출.
    final_status: "completed" | "failed"  (Job 기준 소문자)
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app

    sim_expected = final_status.upper()   # Simulation 모델은 대문자 상태

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        # ── GET /simulations/{sim_id} ──────────────────────────
        r = await client.get(f"/simulations/{sim_id}")
        assert r.status_code == 200, (
            f"GET /simulations/{sim_id} → {r.status_code}: {r.text[:200]}"
        )
        sim_data = r.json()
        assert sim_data["id"] == sim_id
        assert sim_data["status"] == sim_expected, (
            f"sim status 불일치: {sim_data['status']} != {sim_expected}"
        )
        print(f"  [OK] GET /simulations/{sim_id} → status={sim_data['status']}")

        # ── GET /jobs/{job_id} ────────────────────────────────
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, (
            f"GET /jobs/{job_id} → {r.status_code}: {r.text[:200]}"
        )
        job_data = r.json()
        assert job_data["status"] == final_status, (
            f"job status 불일치: {job_data['status']} != {final_status}"
        )

        if final_status == "completed":
            assert job_data["result_id"] == sim_id, (
                f"result_id 불일치: {job_data['result_id']} != {sim_id}"
            )
            assert job_data["error_message"] is None
        else:  # failed
            assert job_data["result_id"] is None
            assert "[" in (job_data.get("error_message") or ""), (
                f"error_message에 [ERROR_CODE] prefix 없음: {job_data.get('error_message')}"
            )

        print(
            f"  [OK] GET /jobs/{job_id} → "
            f"status={job_data['status']}, result_id={job_data.get('result_id')}"
        )


# ──────────────────────────────────────────────────────────────
# 케이스 1: 성공 Mock (TX1 → TX2)
# ──────────────────────────────────────────────────────────────

async def case1_success_mock() -> bool:
    """mock 엔진 결과 반환 → TX1→TX2, COMPLETED, output_json 저장 확인."""
    header("케이스 1: 성공 Mock (TX1 → TX2)")

    from app.services.simulation_service import SimulationService
    from tasks.simulation_task import _tx1_processing, _tx2_completed

    sim_id, job_id = await _create_sim_and_job("돼지야_pipeline")
    print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

    # Session A: TX1
    await _tx1_processing(job_id)
    _, job = await _verify(sim_id, job_id)
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status.value}")

    with patch.object(
        SimulationService, "run", AsyncMock(return_value=MOCK_SERVICE_RESULT)
    ):
        result = await SimulationService().run(
            store_source_id="돼지야_pipeline",
            selected_strategy_ids=[],
            days=1, seed=42, agent_count=5, max_concurrent_llm_calls=5, api_key="",
        )

    output_json = {**result["output"], "meta": result["meta"]}
    serialized  = json.dumps(output_json, ensure_ascii=False)

    # Session A: TX2
    await _tx2_completed(job_id, sim_id, output_json)

    # Session B: 상태 검증
    sim, job = await _verify(sim_id, job_id)
    assert sim.status == "COMPLETED",        f"TX2 후 sim.status 불일치: {sim.status}"
    assert job.status.value == "completed",  f"TX2 후 job.status 불일치: {job.status}"
    assert job.result_id == sim_id,          f"job.result_id 불일치: {job.result_id} != {sim_id}"
    assert sim.output_json is not None,      "output_json 미저장"
    assert sim.output_json["meta"]["engine"] == "run_full_pipeline", \
        f"meta.engine 불일치: {sim.output_json['meta'].get('engine')}"
    assert sim.output_json["before"]["visit_count"] == 100, \
        f"before.visit_count 불일치: {sim.output_json['before'].get('visit_count')}"

    print(f"  [OK] TX2: sim.status={sim.status}, job.status={job.status.value}")
    print(f"  [OK] output_json 저장 확인 (engine={sim.output_json['meta']['engine']})")
    print(f"  [OK] output_json JSON-serializable ({len(serialized)} chars)")

    await _cleanup(sim_id, job_id)
    print("  [PASS] 케이스 1 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 2: 실패 Mock (TX1 → TX3)
# ──────────────────────────────────────────────────────────────

async def case2_failure_mock() -> bool:
    """mock 엔진 예외 발생 → TX1→TX3, FAILED, [BACKEND_TIMEOUT] prefix + output_json 미저장 확인."""
    header("케이스 2: 실패 Mock (TX1 → TX3)")

    from app.services.simulation_service import SimulationService
    from tasks.simulation_task import _tx1_processing, _tx3_failed

    sim_id, job_id = await _create_sim_and_job("__mock_fail__")
    print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

    await _tx1_processing(job_id)
    _, job = await _verify(sim_id, job_id)
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status.value}")

    context   = f"store=__mock_fail__, sim_id={sim_id}, job_id={job_id}"
    error_msg = ""
    with patch.object(
        SimulationService, "run",
        AsyncMock(side_effect=RuntimeError("[BACKEND_TIMEOUT] 시뮬레이션 시간 초과 mock")),
    ):
        try:
            await SimulationService().run(
                store_source_id="__mock_fail__",
                selected_strategy_ids=[],
                days=1, seed=42, agent_count=5, max_concurrent_llm_calls=5, api_key="",
            )
            print("  [FAIL] 예외가 발생하지 않음 (RuntimeError 예상)")
            return False
        except RuntimeError as e:
            error_msg = f"{str(e)[:550]} | Context: {context}"
            print(f"  [OK] RuntimeError 발생: {str(e)[:70]}")

    await _tx3_failed(job_id, sim_id, error_msg)

    sim, job = await _verify(sim_id, job_id)
    assert sim.status == "FAILED",           f"TX3 후 sim.status 불일치: {sim.status}"
    assert job.status.value == "failed",     f"TX3 후 job.status 불일치: {job.status}"
    assert sim.output_json is None, \
        f"output_json이 저장되면 안 됨: {sim.output_json}"
    assert "[BACKEND_TIMEOUT]" in (sim.error_message or ""), \
        f"error_message에 [BACKEND_TIMEOUT] 없음: {sim.error_message}"

    print(f"  [OK] TX3: sim.status={sim.status}, job.status={job.status.value}")
    print(f"  [OK] output_json 미저장 확인 (output_json is None)")
    print(f"  [OK] error_message prefix: {(sim.error_message or '')[:70]}")

    await _cleanup(sim_id, job_id)
    print("  [PASS] 케이스 2 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# Real Engine 케이스 (ENGINE_MODE=real 전용)
# ──────────────────────────────────────────────────────────────

async def case_real_engine() -> bool:
    """실제 SimulationService.run() 호출.

    성공(TX2)·실패(TX3) 모두 허용 — 3-TX 패턴 일관성과 JSON 직렬화 가능성을 검증한다.
    완료 후 in-process AsyncClient로 GET /simulations/{id}, GET /jobs/{id} 검증.
    cleanup은 finally에서 항상 수행한다.
    """
    header("Real Engine 케이스 (3-TX + API 검증)")

    from app.services.simulation_service import SimulationService
    from tasks.simulation_task import _tx1_processing, _tx2_completed, _tx3_failed

    store_id, x_report_id = await _resolve_store_and_xreport()
    print(f"  [OK] store_id={store_id}, x_report_id={x_report_id}")

    sim_id:       Optional[int] = None
    job_id:       Optional[int] = None
    final_status: Optional[str] = None

    try:
        sim_id, job_id = await _create_sim_and_job(
            store_id=store_id,
            x_report_id=x_report_id,
            days=SIM_DAYS,
            agent_count=SIM_AGENT_COUNT,
        )
        print(f"  [OK] Simulation {sim_id}, Job {job_id} 생성")

        # ── TX1: Job → PROCESSING ──────────────────────────────
        await _tx1_processing(job_id)
        _, job = await _verify(sim_id, job_id)
        assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
        print(f"  [OK] TX1: job.status = processing")

        # ── 실제 엔진 호출 ─────────────────────────────────────
        print(
            f"  [..] 엔진 실행 중 "
            f"(SIM_DAYS={SIM_DAYS}, agent_count={SIM_AGENT_COUNT})..."
        )
        try:
            result = await SimulationService().run(
                store_source_id=store_id,
                selected_strategy_ids=[],
                days=SIM_DAYS,
                seed=42,
                agent_count=SIM_AGENT_COUNT,
                max_concurrent_llm_calls=MAX_CONCURRENT_LLM_CALLS,
                api_key=settings.OPENAI_API_KEY,
            )

            # ── TX2: COMPLETED ─────────────────────────────────
            output_json: dict = {**result["output"], "meta": result["meta"]}
            await _tx2_completed(job_id, sim_id, output_json)

            # Session B: 성공 검증
            sim, job = await _verify(sim_id, job_id)
            assert sim.status == "COMPLETED",       f"TX2 후 sim.status 불일치: {sim.status}"
            assert job.status.value == "completed", f"TX2 후 job.status 불일치: {job.status}"
            assert job.result_id == sim_id,         f"job.result_id 불일치: {job.result_id} != {sim_id}"
            assert sim.output_json is not None,     "output_json 미저장"

            serialized = json.dumps(sim.output_json, ensure_ascii=False)
            timing_ms  = result["meta"].get("timing_ms", "?")
            print(
                f"  [OK] TX2: COMPLETED — "
                f"output_json {len(serialized)} chars (JSON-serializable)"
            )
            print(f"  [OK] engine timing_ms={timing_ms}")
            final_status = "completed"

        except RuntimeError as e:
            # ── TX3: FAILED ────────────────────────────────────
            # SimulationService가 이미 [ERROR_CODE] 형식으로 래핑한 에러
            context   = f"store={store_id}, sim_id={sim_id}, job_id={job_id}"
            error_msg = f"{str(e)[:550]} | Context: {context}"
            await _tx3_failed(job_id, sim_id, error_msg)

            # Session B: 실패 검증
            sim, job = await _verify(sim_id, job_id)
            assert sim.status == "FAILED",       f"TX3 후 sim.status 불일치: {sim.status}"
            assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
            assert sim.output_json is None, \
                f"FAILED 시 output_json이 저장되면 안 됨: {sim.output_json}"
            assert "[" in (sim.error_message or ""), \
                f"error_message에 [ERROR_CODE] prefix 없음: {sim.error_message}"

            print(f"  [OK] TX3 (엔진 실패 — 정상 실패 경로): {error_msg[:80]}")
            final_status = "failed"

        # ── in-process API 검증 ─────────────────────────────────
        await _verify_api(sim_id, job_id, final_status)

        print(f"  [PASS] Real Engine 케이스 성공 (결과: {final_status})\n")
        return True

    finally:
        if sim_id is not None and job_id is not None:
            await _cleanup(sim_id, job_id)


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main() -> None:
    if ENGINE_MODE == "real":
        header("Simulation 파이프라인 통합 검증 (Real Engine 모드)")
        print("  실제 SimulationService.run() 호출 + 3-TX 전환 + API 응답 검증")
        print(
            f"  ENGINE_MODE=real  STORE_ID={STORE_ID or '(DB 자동)'}  "
            f"SIM_DAYS={SIM_DAYS}  agent_count={SIM_AGENT_COUNT}"
        )
        cases = [(case_real_engine, "Real Engine 케이스")]
    else:
        header("Simulation 파이프라인 통합 검증 (Mock 모드)")
        print("  uvicorn/Celery 없이 3-TX 전환 + DB 저장 검증")
        print(f"  ENGINE_MODE=mock  LOG_LEVEL={LOG_LEVEL}  SQL_ECHO={SQL_ECHO}")
        cases = [
            (case1_success_mock, "케이스 1"),
            (case2_failure_mock, "케이스 2"),
        ]

    results = []
    for coro, name in cases:
        try:
            results.append(await coro())
        except Exception as e:
            import traceback
            print(f"  [FAIL] {name} 예외: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append(False)

    header("최종 결과")
    passed = sum(results)
    total  = len(results)
    print(f"  {passed}/{total} PASS")

    if passed == total:
        print("  SIM_PIPELINE_OK")
        sys.exit(0)
    else:
        print("  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
