"""
Simulation Background Task

3-TX 패턴: TX1(PROCESSING) → TX2(COMPLETED) → TX3(FAILED)

Celery 제거 — FastAPI asyncio.create_task() 로 직접 실행.
SimulationService를 통해 엔진을 호출하고, 각 TX에서 DB 상태를 원자적으로 전환한다.

에러 코드:
  [BACKEND_TIMEOUT] — asyncio.TimeoutError (엔진 응답 없음)
  [UNKNOWN_ERROR]   — 그 외 예외 (파이프라인 내부 오류, 입력 오류 등)

에러 메시지 포맷:
  [ERROR_CODE] message | Context: store=..., sim_id=..., job_id=...
"""
import asyncio
from typing import Any, Dict, List

from app.core.config import settings
from app.db.session import get_async_session
from app.repositories.job_repo import JobRepo
from app.repositories.simulation_repo import SimulationRepo
from app.models.job import JobStatus


# ──────────────────────────────────────────────────────────────
# 에러 포맷 헬퍼
# ──────────────────────────────────────────────────────────────

def _format_error(error_code: str, message: str, context: str) -> str:
    """에러 메시지 포맷: [ERROR_CODE] message | Context: ..."""
    return f"[{error_code}] {message[:400]} | Context: {context}"


# ──────────────────────────────────────────────────────────────
# DB 직접 접근 Async 헬퍼 (TX별 독립 트랜잭션)
# ──────────────────────────────────────────────────────────────

async def _tx1_processing(job_id: int) -> None:
    """[TX1] Job → PROCESSING"""
    async with get_async_session() as session:
        async with session.begin():
            await JobRepo(session).update_status(
                job_id=job_id, status=JobStatus.PROCESSING, progress=5
            )
    print(f"[SimTask TX1] Job {job_id} → PROCESSING")


async def _tx2_completed(
    job_id: int,
    sim_id: int,
    output_json: Dict[str, Any],
) -> None:
    """[TX2] Simulation COMPLETED + Job COMPLETED (단일 원자적 트랜잭션)"""
    async with get_async_session() as session:
        async with session.begin():
            await SimulationRepo(session).update_completed(sim_id, output_json)
            await JobRepo(session).update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=sim_id,
                progress=100,
            )
    print(f"[SimTask TX2] Simulation {sim_id} + Job {job_id} → COMPLETED")


async def _tx3_failed(job_id: int, sim_id: int, error_message: str) -> None:
    """[TX3] Simulation FAILED + Job FAILED (별도 독립 트랜잭션)"""
    try:
        async with get_async_session() as session:
            async with session.begin():
                await SimulationRepo(session).update_failed(sim_id, error_message)
                await JobRepo(session).update_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error_message=error_message,
                )
        print(f"[SimTask TX3] Simulation {sim_id} + Job {job_id} → FAILED")
    except Exception as e:
        print(f"[SimTask TX3 CRITICAL] TX3 자체 실패: {e}")


# ──────────────────────────────────────────────────────────────
# Background Task (Celery 없음 — asyncio.create_task 로 호출)
# ──────────────────────────────────────────────────────────────

async def run_simulation(
    job_id: int,
    sim_id: int,
    store_source_id: str,
    selected_strategy_ids: List[str],
    days: int,
    seed: int,
    agent_count: int,
    max_concurrent_llm_calls: int = 5,
) -> Dict[str, Any]:
    """
    시뮬레이션 Background Task.

    asyncio.create_task(run_simulation(...)) 로 호출한다.
    """
    print(f"[SimTask] 시작: store={store_source_id}, sim_id={sim_id}, job_id={job_id}")
    context = f"store={store_source_id}, sim_id={sim_id}, job_id={job_id}"

    try:
        # ── TX1: Job → PROCESSING ──────────────────────────────
        await _tx1_processing(job_id)

        # ── 시뮬레이션 실행 (트랜잭션 외부) ─────────────────────
        from app.services.simulation_service import SimulationService

        result = await SimulationService().run(
            store_source_id=store_source_id,
            selected_strategy_ids=selected_strategy_ids,
            days=days,
            seed=seed,
            agent_count=agent_count,
            max_concurrent_llm_calls=max_concurrent_llm_calls,
            api_key=settings.OPENAI_API_KEY,
        )

        output_json: Dict[str, Any] = {
            **result["output"],
            "meta": result["meta"],
        }

        # ── TX2: Simulation + Job → COMPLETED ─────────────────
        await _tx2_completed(job_id, sim_id, output_json)

        timing_ms = result["meta"].get("timing_ms", "?")
        print(f"[SimTask] 완료: sim_id={sim_id}, timing_ms={timing_ms}ms")
        return {"sim_id": sim_id, "status": "completed"}

    except RuntimeError as e:
        error_msg = f"{str(e)[:550]} | Context: {context}"
        await _tx3_failed(job_id, sim_id, error_msg)
        print(f"[SimTask] 실패(RuntimeError): {error_msg[:200]}")
        raise

    except Exception as e:
        error_msg = _format_error(
            "UNKNOWN_ERROR",
            f"{type(e).__name__}: {str(e)}",
            context,
        )
        await _tx3_failed(job_id, sim_id, error_msg)
        print(f"[SimTask] 실패(Exception): {error_msg[:200]}")
        raise
