"""
Y-Report 파이프라인 통합 검증 스크립트

# ── 실행 커맨드 ────────────────────────────────────────────────
#
#   [mock 모드]  — 기본, 실제 엔진 없이 3-TX 전환 + DB 저장 검증
#   python scripts/verify_y_report_pipeline.py
#   LOG_LEVEL=DEBUG SQL_ECHO=1 python scripts/verify_y_report_pipeline.py
#
#   [real 모드]  — 실제 YReportAdapter/ComparisonReportGenerator.generate_all() 1회 호출
#   ENGINE_MODE=real python scripts/verify_y_report_pipeline.py
#   ENGINE_MODE=real SIMULATION_ID=5 python scripts/verify_y_report_pipeline.py
#   (SIMULATION_ID 생략 시 DB에서 가장 최근 COMPLETED Simulation 자동 사용;
#    없으면 FIXTURE_SIM_OUTPUT으로 mock fixture를 생성하여 사용)

uvicorn/Celery worker 없이 로컬에서 바로 실행.

Mock 모드:
  Fixture: DB에 COMPLETED Simulation 레코드를 미리 생성하여
  YReportService가 실제 DB 조회를 거치도록 한다.
  YReportAdapter.generate()를 mock하여 실제 파일시스템 없이
  3-TX 흐름 및 DB 저장을 검증한다.

  케이스 1: 성공 — mock 어댑터 결과 반환
      → TX1(PROCESSING) → TX2(COMPLETED)
      → y_report COMPLETED, output_json + markdown 저장 확인

  케이스 2: 실패 — mock 어댑터 FileNotFoundError 발생
      → TX1(PROCESSING) → TX3(FAILED)
      → y_report FAILED, [MISSING_DATA] prefix 확인, output_json 미저장 확인

Real 모드:
  실제 YReportService().run()을 호출하여 엔진 실행 + DB 저장을 검증한다.
  성공/실패 모두 허용 — 3-TX 패턴 일관성 + JSON 직렬화 가능성을 검증한다.
  완료 후 in-process AsyncClient로 GET /y-reports/{id}, GET /jobs/{id} 응답을 검증한다.

세션 분리 원칙:
  Session A: _tx* 헬퍼 + YReportService 내부에서 독립 생성 (Task 실행 세션)
  Session B: 케이스별 검증 및 cleanup 전용 세션 (별도 context manager)

cleanup:
  각 케이스 완료 후 YReport + Job 삭제.
  Simulation fixture는 main() 종료 시 최종 삭제.

완료 조건: 두 케이스 모두 PASS 출력 (mock) / 1케이스 PASS (real)
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


# ── 환경 변수 적용 (엔진 생성 전) ─────────────────────────────────
# LOG_LEVEL:     Python logging 레벨 (DEBUG / INFO / WARNING 등, 기본 WARNING)
# SQL_ECHO:      "1"로 설정 시 SQLAlchemy가 실행 쿼리를 출력 (기본 OFF)
# ENGINE_MODE:   mock(기본) | real
# SIMULATION_ID: real 모드 전용 — 사용할 COMPLETED Simulation ID (생략 가능)
# STORE_ID:      real 모드에서 fixture 생성 시 사용할 매장명 (생략 가능)
# X_REPORT_ID:   real 모드에서 YReportService에 전달할 XReport ID (생략 가능)

LOG_LEVEL     = os.environ.get("LOG_LEVEL", "WARNING").upper()
SQL_ECHO      = os.environ.get("SQL_ECHO", "0") == "1"
ENGINE_MODE   = os.environ.get("ENGINE_MODE", "mock").lower()
STORE_ID      = os.environ.get("STORE_ID", "")
SIMULATION_ID_STR = os.environ.get("SIMULATION_ID", "")
X_REPORT_ID_STR   = os.environ.get("X_REPORT_ID", "")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# SQL_ECHO를 settings에 반영한 뒤 엔진을 재생성하도록 reset_engine() 호출
from app.core.config import settings  # noqa: E402
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
# Mock 데이터 — YReportAdapter.generate() 반환값 형식과 일치
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
        "target_store": "돼지야_yr_pipeline",
        "before_dir": "/mock/before",
        "after_dir": "/mock/after",
        "report_path": "/mock/after/y_report/Comparison_Report.md",
        "x_report_keys": [],
    },
}

# Simulation fixture output_json (YReportService가 DB에서 로드)
FIXTURE_SIM_OUTPUT = {
    "target_store": "돼지야_yr_pipeline",
    "before": {"output_dir": "/mock/before", "visit_count": 100},
    "after": {"output_dir": "/mock/after", "visit_count": 120},
}


# ──────────────────────────────────────────────────────────────
# DB 헬퍼 (Session A / Session B 명시)
# ──────────────────────────────────────────────────────────────

async def _create_fixture_sim(store_id: str) -> int:
    """[Session A-init] COMPLETED Simulation fixture를 DB에 생성한다.

    YReportService.run()이 실제 DB 조회를 통해 before/after 경로를 로드하도록
    create_completed()로 COMPLETED 레코드를 직접 삽입한다.
    """
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_completed(
                store_source_id=store_id,
                x_report_id=None,
                selected_strategy_ids=[],
                days=1,
                seed=42,
                output_json=FIXTURE_SIM_OUTPUT,
            )
    return sim.id


async def _create_yr_and_job(store_id: str, sim_id: int) -> tuple:  # (yr_id, job_id)
    """[Session A-init] PENDING YReport + Job 레코드를 함께 생성한다."""
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo
    from app.models.job import JobType

    async with get_async_session() as session:
        async with session.begin():
            yr = await YReportRepo(session).create_pending(
                store_source_id=store_id,
                simulation_id=sim_id,
                input_json={"verify": True},
            )
            job = await JobRepo(session).create(
                job_type=JobType.Y_REPORT,
                store_source_id=store_id,
            )
    return yr.id, job.id


async def _verify_yr(yr_id: int, job_id: int) -> tuple:  # (YReport, Job)
    """[Session B] YReport + Job을 독립 세션으로 조회한다."""
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        yr = await YReportRepo(session).get_by_id(yr_id)
        job = await JobRepo(session).get_by_id(job_id)
    return yr, job


async def _cleanup_yr_and_job(yr_id: int, job_id: int) -> None:
    """[Session B] YReport + Job 레코드를 삭제한다."""
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        async with session.begin():
            yr = await YReportRepo(session).get_by_id(yr_id)
            if yr:
                await session.delete(yr)
            job = await JobRepo(session).get_by_id(job_id)
            if job:
                await session.delete(job)
    print(f"  [OK] cleanup: YReport {yr_id}, Job {job_id} 삭제")


async def _cleanup_sim(sim_id: int) -> None:
    """[Session B] Simulation fixture 레코드를 삭제한다."""
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).get_by_id(sim_id)
            if sim:
                await session.delete(sim)
    print(f"  [OK] cleanup: Simulation fixture {sim_id} 삭제")


# ──────────────────────────────────────────────────────────────
# Real 모드 전용 헬퍼
# ──────────────────────────────────────────────────────────────

async def _resolve_simulation() -> Tuple[int, bool]:
    """sim_id 와 is_fixture 를 결정한다.

    우선순위:
    1) SIMULATION_ID 환경변수 → 해당 Simulation을 DB에서 조회 (COMPLETED 필수)
    2) 없으면 DB에서 가장 최근 COMPLETED Simulation을 자동 조회
    3) 없으면 FIXTURE_SIM_OUTPUT으로 mock fixture를 새로 생성

    Returns:
        (sim_id, is_fixture)  is_fixture=True 이면 main()이 종료 시 삭제한다.
    """
    from sqlalchemy import select, desc
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.models.simulation import Simulation

    # 1) SIMULATION_ID 환경변수 직접 지정
    if SIMULATION_ID_STR:
        sim_id_int = int(SIMULATION_ID_STR)
        async with get_async_session() as session:
            sim = await SimulationRepo(session).get_by_id(sim_id_int)
        if not sim:
            raise ValueError(f"SIMULATION_ID={sim_id_int} DB에 없음")
        if sim.status != "COMPLETED":
            raise ValueError(
                f"SIMULATION_ID={sim_id_int} 상태가 COMPLETED가 아님: {sim.status}\n"
                "COMPLETED 상태의 Simulation ID를 지정하세요."
            )
        print(f"  [ENV] SIMULATION_ID={sim_id_int} 사용 (store={sim.store_source_id})")
        return sim_id_int, False

    # 2) DB에서 가장 최근 COMPLETED Simulation 자동 조회
    async with get_async_session() as session:
        stmt = (
            select(Simulation)
            .where(Simulation.status == "COMPLETED")
            .order_by(desc(Simulation.created_at))
            .limit(1)
        )
        result = await session.execute(stmt)
        sim = result.scalar_one_or_none()

    if sim:
        print(
            f"  [AUTO] DB 최근 COMPLETED Simulation 사용: "
            f"id={sim.id}, store={sim.store_source_id}"
        )
        return sim.id, False

    # 3) fixture 생성 (DB에 COMPLETED Simulation 없음)
    store_id = STORE_ID or "돼지야_yr_pipeline"
    sim_id = await _create_fixture_sim(store_id)
    print(f"  [FIXTURE] COMPLETED Simulation {sim_id} 생성 (store={store_id}, mock 경로)")
    return sim_id, True


async def _verify_api_yr(yr_id: int, job_id: int, final_status: str) -> None:
    """[in-process] GET /y-reports/{id} + GET /jobs/{id} 응답을 검증한다.

    서버 프로세스 없이 httpx.AsyncClient + ASGITransport로 in-process 호출.
    final_status: "completed" | "failed"  (Job 기준 소문자)
    """
    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app

    yr_expected = final_status.upper()   # YReport 모델은 대문자 상태

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        # ── GET /y-reports/{yr_id} ────────────────────────────
        r = await client.get(f"/y-reports/{yr_id}")
        assert r.status_code == 200, (
            f"GET /y-reports/{yr_id} → {r.status_code}: {r.text[:200]}"
        )
        yr_data = r.json()
        assert yr_data["id"] == yr_id
        assert yr_data["status"] == yr_expected, (
            f"yr status 불일치: {yr_data['status']} != {yr_expected}"
        )
        print(f"  [OK] GET /y-reports/{yr_id} → status={yr_data['status']}")

        if final_status == "completed":
            assert yr_data["output_json"] is not None, "output_json 없음"
            assert yr_data["markdown"] is not None, "markdown 없음"
        else:  # failed
            assert yr_data["output_json"] is None, (
                f"FAILED 시 output_json이 있으면 안 됨: {yr_data.get('output_json')}"
            )
            assert "[" in (yr_data.get("error_message") or ""), (
                f"error_message에 [ERROR_CODE] prefix 없음: {yr_data.get('error_message')}"
            )

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
            assert job_data["result_id"] == yr_id, (
                f"result_id 불일치: {job_data['result_id']} != {yr_id}"
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

async def case1_success_mock(sim_id: int) -> bool:
    """mock 어댑터 결과 반환 → TX1→TX2, y_report COMPLETED, output_json+markdown 저장 확인."""
    header("케이스 1: 성공 Mock (TX1 → TX2)")

    from app.adapters.y_report_adapter import YReportAdapter
    from app.services.y_report_service import YReportService
    from tasks.y_report_task import _tx1_processing, _tx2_completed

    yr_id, job_id = await _create_yr_and_job("돼지야_yr_pipeline", sim_id)
    print(f"  [OK] YReport {yr_id}, Job {job_id} 생성 (Simulation fixture: {sim_id})")

    # Session A: TX1 — Job → PROCESSING
    await _tx1_processing(job_id)
    _, job = await _verify_yr(yr_id, job_id)  # Session B
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status.value}")

    # mock 어댑터 — YReportService가 DB에서 Simulation을 실제로 조회한 뒤 adapter만 mock
    with patch.object(
        YReportAdapter, "generate", AsyncMock(return_value=MOCK_ADAPTER_RESULT)
    ):
        result = await YReportService().run(
            simulation_id=sim_id,
            x_report_id=None,
            options={},
        )

    # y_report_task.py와 동일한 output_json 포맷
    output_json = {
        "structured": result["output"].get("structured", {}),
        "adapter_meta": result["output"].get("meta", {}),
        "service_meta": result["meta"],
    }
    markdown = result["output"].get("markdown", "")
    serialized = json.dumps(output_json, ensure_ascii=False)

    # Session A: TX2 — YReport COMPLETED + Job COMPLETED (원자적)
    await _tx2_completed(job_id, yr_id, output_json, markdown)

    # Session B: 상태 검증
    yr, job = await _verify_yr(yr_id, job_id)
    assert yr.status == "COMPLETED", f"TX2 후 yr.status 불일치: {yr.status}"
    assert job.status.value == "completed", f"TX2 후 job.status 불일치: {job.status}"
    assert job.result_id == yr_id, f"job.result_id 불일치: {job.result_id} != {yr_id}"
    assert yr.output_json is not None, "output_json 미저장"
    assert yr.output_json["structured"]["visit_change_pct"] == 20.0, \
        f"visit_change_pct 불일치: {yr.output_json['structured'].get('visit_change_pct')}"
    assert yr.output_json["service_meta"]["engine"] == "YReportAdapter", \
        f"service_meta.engine 불일치: {yr.output_json['service_meta'].get('engine')}"
    assert yr.markdown is not None, "markdown 미저장"
    assert "방문율" in yr.markdown, "markdown 내용 불일치"
    assert json.dumps(yr.output_json, ensure_ascii=False)  # 직렬화 계약 검증

    print(f"  [OK] TX2: yr.status={yr.status}, job.status={job.status.value}")
    print(f"  [OK] output_json 저장 확인 "
          f"(visit_change_pct={yr.output_json['structured']['visit_change_pct']}%)")
    print(f"  [OK] output_json JSON-serializable ({len(serialized)} chars)")
    print(f"  [OK] markdown: {len(yr.markdown)} chars, starts='{yr.markdown[:20]}'")

    await _cleanup_yr_and_job(yr_id, job_id)
    print("  [PASS] 케이스 1 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# 케이스 2: 실패 Mock (TX1 → TX3)
# ──────────────────────────────────────────────────────────────

async def case2_failure_mock(sim_id: int) -> bool:
    """mock 어댑터 FileNotFoundError → TX1→TX3, FAILED, [MISSING_DATA] prefix, output_json 미저장 확인."""
    header("케이스 2: 실패 Mock (TX1 → TX3)")

    from app.adapters.y_report_adapter import YReportAdapter
    from app.services.y_report_service import YReportService
    from tasks.y_report_task import _tx1_processing, _tx3_failed

    yr_id, job_id = await _create_yr_and_job("돼지야_yr_pipeline", sim_id)
    print(f"  [OK] YReport {yr_id}, Job {job_id} 생성 (Simulation fixture: {sim_id})")

    # Session A: TX1 — Job → PROCESSING
    await _tx1_processing(job_id)
    _, job = await _verify_yr(yr_id, job_id)  # Session B
    assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
    print(f"  [OK] TX1: job.status = {job.status.value}")

    # mock 어댑터 → FileNotFoundError (YReportService가 [MISSING_DATA]로 래핑)
    context = f"y_report_id={yr_id}"
    error_msg = ""
    with patch.object(
        YReportAdapter,
        "generate",
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
            # y_report_task.py RuntimeError 핸들러와 동일한 포맷
            error_msg = f"{str(e)[:550]} | Context: {context}"
            print(f"  [OK] RuntimeError 발생: {str(e)[:70]}")

    # Session A: TX3 — YReport FAILED + Job FAILED (원자적)
    await _tx3_failed(job_id, yr_id, error_msg)

    # Session B: 상태 검증
    yr, job = await _verify_yr(yr_id, job_id)
    assert yr.status == "FAILED", f"TX3 후 yr.status 불일치: {yr.status}"
    assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
    assert yr.output_json is None, \
        f"output_json이 저장되면 안 됨 (미저장 확인): {yr.output_json}"
    assert "[MISSING_DATA]" in (yr.error_message or ""), \
        f"error_message에 [MISSING_DATA] 없음: {yr.error_message}"

    print(f"  [OK] TX3: yr.status={yr.status}, job.status={job.status.value}")
    print(f"  [OK] output_json 미저장 확인 (output_json is None)")
    print(f"  [OK] error_message prefix: {(yr.error_message or '')[:70]}")

    await _cleanup_yr_and_job(yr_id, job_id)
    print("  [PASS] 케이스 2 성공\n")
    return True


# ──────────────────────────────────────────────────────────────
# Real Engine 케이스 (ENGINE_MODE=real 전용)
# ──────────────────────────────────────────────────────────────

async def case_real_engine(sim_id: int) -> bool:
    """실제 YReportService().run() 호출.

    성공(TX2)·실패(TX3) 모두 허용 — 3-TX 패턴 일관성과 JSON 직렬화 가능성을 검증한다.
    완료 후 in-process AsyncClient로 GET /y-reports/{id}, GET /jobs/{id} 검증.
    cleanup은 finally에서 항상 수행한다.
    """
    header("Real Engine 케이스 (3-TX + API 검증)")

    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.services.y_report_service import YReportService
    from tasks.y_report_task import _tx1_processing, _tx2_completed, _tx3_failed

    print(f"  [OK] simulation_id={sim_id}")

    yr_id:        Optional[int] = None
    job_id:       Optional[int] = None
    final_status: Optional[str] = None

    try:
        # store_source_id 조회 (YReport + Job 생성에 필요)
        async with get_async_session() as session:
            sim = await SimulationRepo(session).get_by_id(sim_id)
        store_id = sim.store_source_id if sim else "yr_real_verify"

        yr_id, job_id = await _create_yr_and_job(store_id, sim_id)
        print(f"  [OK] YReport {yr_id}, Job {job_id} 생성 (store={store_id})")

        # ── TX1: Job → PROCESSING ──────────────────────────────
        await _tx1_processing(job_id)
        _, job = await _verify_yr(yr_id, job_id)
        assert job.status.value == "processing", f"TX1 후 job.status 불일치: {job.status}"
        print(f"  [OK] TX1: job.status = processing")

        # ── 실제 엔진 호출 ─────────────────────────────────────
        x_report_id_int: Optional[int] = int(X_REPORT_ID_STR) if X_REPORT_ID_STR else None
        print(
            f"  [..] YReportService 실행 중 "
            f"(simulation_id={sim_id}, x_report_id={x_report_id_int})..."
        )
        try:
            result = await YReportService().run(
                simulation_id=sim_id,
                x_report_id=x_report_id_int,
                options={},
            )

            # ── TX2: COMPLETED ─────────────────────────────────
            output_json: dict = {
                "structured": result["output"].get("structured", {}),
                "adapter_meta": result["output"].get("meta", {}),
                "service_meta": result["meta"],
            }
            markdown: str = result["output"].get("markdown", "")
            await _tx2_completed(job_id, yr_id, output_json, markdown)

            # Session B: 성공 검증
            yr, job = await _verify_yr(yr_id, job_id)
            assert yr.status == "COMPLETED",        f"TX2 후 yr.status 불일치: {yr.status}"
            assert job.status.value == "completed", f"TX2 후 job.status 불일치: {job.status}"
            assert job.result_id == yr_id,          f"job.result_id 불일치: {job.result_id} != {yr_id}"
            assert yr.output_json is not None,      "output_json 미저장"

            serialized = json.dumps(yr.output_json, ensure_ascii=False)
            timing_ms  = result["meta"].get("timing_ms", "?")
            print(
                f"  [OK] TX2: COMPLETED — "
                f"output_json {len(serialized)} chars (JSON-serializable)"
            )
            print(f"  [OK] engine timing_ms={timing_ms}")
            final_status = "completed"

        except RuntimeError as e:
            # ── TX3: FAILED ────────────────────────────────────
            # YReportService가 이미 [ERROR_CODE] 형식으로 래핑한 에러
            context   = f"yr_id={yr_id}, simulation_id={sim_id}, job_id={job_id}"
            error_msg = f"{str(e)[:550]} | Context: {context}"
            await _tx3_failed(job_id, yr_id, error_msg)

            # Session B: 실패 검증
            yr, job = await _verify_yr(yr_id, job_id)
            assert yr.status == "FAILED",        f"TX3 후 yr.status 불일치: {yr.status}"
            assert job.status.value == "failed", f"TX3 후 job.status 불일치: {job.status}"
            assert yr.output_json is None, \
                f"FAILED 시 output_json이 저장되면 안 됨: {yr.output_json}"
            assert "[" in (yr.error_message or ""), \
                f"error_message에 [ERROR_CODE] prefix 없음: {yr.error_message}"

            print(f"  [OK] TX3 (엔진 실패 — 정상 실패 경로): {error_msg[:80]}")
            final_status = "failed"

        # ── in-process API 검증 ─────────────────────────────────
        await _verify_api_yr(yr_id, job_id, final_status)

        print(f"  [PASS] Real Engine 케이스 성공 (결과: {final_status})\n")
        return True

    finally:
        if yr_id is not None and job_id is not None:
            await _cleanup_yr_and_job(yr_id, job_id)


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main() -> None:
    if ENGINE_MODE == "real":
        header("Y-Report 파이프라인 통합 검증 (Real Engine 모드)")
        print("  실제 YReportService().run() 호출 + 3-TX 전환 + API 응답 검증")
        print(
            f"  ENGINE_MODE=real  SIMULATION_ID={SIMULATION_ID_STR or '(DB 자동)'}  "
            f"LOG_LEVEL={LOG_LEVEL}  SQL_ECHO={SQL_ECHO}"
        )

        # COMPLETED Simulation 결정 (env 지정 → DB 자동 조회 → fixture 생성)
        sim_id, is_fixture = await _resolve_simulation()

        results = []
        try:
            results.append(await case_real_engine(sim_id))
        except Exception as e:
            import traceback
            print(f"  [FAIL] Real Engine 케이스 예외: {type(e).__name__}: {e}")
            traceback.print_exc()
            results.append(False)
        finally:
            if is_fixture:
                await _cleanup_sim(sim_id)

    else:
        header("Y-Report 파이프라인 통합 검증 (Mock 모드)")
        print("  uvicorn/Celery 없이 3-TX 전환 + DB 저장 검증")
        print(f"  LOG_LEVEL={LOG_LEVEL}, SQL_ECHO={SQL_ECHO}")

        # Fixture: COMPLETED Simulation을 DB에 생성 (두 케이스 공유)
        sim_id = await _create_fixture_sim("돼지야_yr_pipeline")
        print(f"\n  [Fixture] COMPLETED Simulation {sim_id} 생성")

        results = []
        for coro_fn, name in [
            (case1_success_mock, "케이스 1"),
            (case2_failure_mock, "케이스 2"),
        ]:
            try:
                results.append(await coro_fn(sim_id))
            except Exception as e:
                import traceback
                print(f"  [FAIL] {name} 예외: {type(e).__name__}: {e}")
                traceback.print_exc()
                results.append(False)

        # Fixture Simulation 삭제 (케이스 공유 레코드이므로 main에서 일괄 삭제)
        await _cleanup_sim(sim_id)

    header("최종 결과")
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} PASS")

    if passed == total:
        print("  Y_REPORT_PIPELINE_OK")
        sys.exit(0)
    else:
        print("  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
