"""
Simulation Celery Task

3-TX 패턴: TX1(PROCESSING) → TX2(COMPLETED) → TX3(FAILED)

SimulationService를 통해 엔진을 호출하고, 각 TX에서 DB 상태를 원자적으로 전환한다.

에러 코드:
  [BACKEND_TIMEOUT] — asyncio.TimeoutError (엔진 응답 없음)
  [UNKNOWN_ERROR]   — 그 외 예외 (파이프라인 내부 오류, 입력 오류 등)

에러 메시지 포맷:
  [ERROR_CODE] message | Context: store=..., sim_id=..., job_id=...
"""
import asyncio
from typing import Any, Dict, List

from celery import Task

from celery_app import celery_app
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
    """[TX3] Simulation FAILED + Job FAILED (별도 독립 트랜잭션)

    TX2가 실패했거나 엔진 오류가 발생한 경우에 호출된다.
    TX3 자체가 실패해도 task 흐름에 영향을 주지 않도록 예외를 내부에서 처리한다.
    """
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
# Celery Task
# ──────────────────────────────────────────────────────────────

class SimulationTask(Task):
    """Simulation Task 기본 클래스"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        print(f"[SimTask ERROR] task_id={task_id}: {exc}")


@celery_app.task(
    bind=True,
    base=SimulationTask,
    name="tasks.run_simulation",
    max_retries=0,  # 시뮬레이션은 비결정적이므로 자동 재시도 안 함
)
def run_simulation(
    self,
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
    시뮬레이션 Celery Task.

    Args:
        job_id: Job ID (jobs 테이블)
        sim_id: Simulation ID (simulations 테이블)
        store_source_id: 타겟 매장명 (예: "돼지야")
        selected_strategy_ids: X_to_Sim 형식 전략 ID (예: ["S1_A", "S2_A"])
        days: 시뮬레이션 기간 (일)
        seed: 랜덤 시드
        agent_count: 에이전트 수
        max_concurrent_llm_calls: 동시 LLM 호출 수 상한 (기본 5)

    Returns:
        {"sim_id": ..., "status": "completed"}
    """
    print(f"[SimTask] 시작: store={store_source_id}, sim_id={sim_id}, job_id={job_id}")
    context = f"store={store_source_id}, sim_id={sim_id}, job_id={job_id}"

    try:
        # ── TX1: Job → PROCESSING ──────────────────────────────
        asyncio.run(_tx1_processing(job_id))

        # ── 시뮬레이션 실행 (트랜잭션 외부) ─────────────────────
        # SimulationService가 에러 분류 및 meta 기록을 담당한다.
        from app.services.simulation_service import SimulationService

        result = asyncio.run(
            SimulationService().run(
                store_source_id=store_source_id,
                selected_strategy_ids=selected_strategy_ids,
                days=days,
                seed=seed,
                agent_count=agent_count,
                max_concurrent_llm_calls=max_concurrent_llm_calls,
                api_key=settings.OPENAI_API_KEY,
            )
        )

        # output_json: 시뮬레이션 결과 + 실행 meta를 함께 저장
        output_json: Dict[str, Any] = {
            **result["output"],
            "meta": result["meta"],
        }

        # ── TX2: Simulation + Job → COMPLETED ─────────────────
        asyncio.run(_tx2_completed(job_id, sim_id, output_json))

        timing_ms = result["meta"].get("timing_ms", "?")
        print(f"[SimTask] 완료: sim_id={sim_id}, timing_ms={timing_ms}ms")
        return {"sim_id": sim_id, "status": "completed"}

    except RuntimeError as e:
        # SimulationService가 이미 [ERROR_CODE] 형식으로 래핑한 에러.
        # str(e) 그대로 사용하고 Context만 덧붙인다 (중복 포맷 방지).
        error_msg = f"{str(e)[:550]} | Context: {context}"
        asyncio.run(_tx3_failed(job_id, sim_id, error_msg))
        print(f"[SimTask] 실패(RuntimeError): {error_msg[:200]}")
        raise

    except Exception as e:
        # 예상치 못한 예외: UNKNOWN_ERROR 코드로 포맷
        error_msg = _format_error(
            "UNKNOWN_ERROR",
            f"{type(e).__name__}: {str(e)}",
            context,
        )
        asyncio.run(_tx3_failed(job_id, sim_id, error_msg))
        print(f"[SimTask] 실패(Exception): {error_msg[:200]}")
        raise
