"""
YReport Background Task

3-TX 패턴: TX1(PROCESSING) → TX2(COMPLETED) → TX3(FAILED)

Celery 제거 — FastAPI asyncio.create_task() 로 직접 실행.
YReportService를 통해 엔진을 호출하고, 각 TX에서 DB 상태를 원자적으로 전환한다.

에러 코드:
  [INVALID_INPUT]   — Simulation 상태 오류 · dirs 없음 등 검증 실패
  [MISSING_DATA]    — before_dir / after_dir 파일시스템 경로 없음
  [BACKEND_TIMEOUT] — asyncio.TimeoutError
  [UNKNOWN_ERROR]   — 그 외 예외

에러 메시지 포맷:
  [ERROR_CODE] message | Context: y_report_id=..., simulation_id=..., job_id=...
"""
import asyncio
from typing import Any, Dict, Optional

from app.db.session import get_async_session
from app.repositories.job_repo import JobRepo
from app.repositories.y_report_repo import YReportRepo
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
    print(f"[YReportTask TX1] Job {job_id} → PROCESSING")


async def _tx2_completed(
    job_id: int,
    y_report_id: int,
    output_json: Dict[str, Any],
    markdown: str,
) -> None:
    """[TX2] YReport COMPLETED + Job COMPLETED (단일 원자적 트랜잭션)"""
    async with get_async_session() as session:
        async with session.begin():
            await YReportRepo(session).update_completed(y_report_id, output_json, markdown)
            await JobRepo(session).update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=y_report_id,
                progress=100,
            )
    print(f"[YReportTask TX2] YReport {y_report_id} + Job {job_id} → COMPLETED")


async def _tx3_failed(job_id: int, y_report_id: int, error_message: str) -> None:
    """[TX3] YReport FAILED + Job FAILED (별도 독립 트랜잭션)

    TX3 자체가 실패해도 task 흐름에 영향을 주지 않도록 예외를 내부에서 처리한다.
    """
    try:
        async with get_async_session() as session:
            async with session.begin():
                await YReportRepo(session).update_failed(y_report_id, error_message)
                await JobRepo(session).update_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error_message=error_message,
                )
        print(f"[YReportTask TX3] YReport {y_report_id} + Job {job_id} → FAILED")
    except Exception as e:
        print(f"[YReportTask TX3 CRITICAL] TX3 자체 실패: {e}")


# ──────────────────────────────────────────────────────────────
# Background Task (Celery 없음 — asyncio.create_task 로 호출)
# ──────────────────────────────────────────────────────────────

async def generate_y_report(
    job_id: int,
    y_report_id: int,
    simulation_id: int,
    x_report_id: Optional[int] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Y-Report 생성 Background Task.

    asyncio.create_task(generate_y_report(...)) 로 호출한다.
    """
    print(
        f"[YReportTask] 시작: y_report_id={y_report_id}, "
        f"simulation_id={simulation_id}, job_id={job_id}"
    )
    context = f"y_report_id={y_report_id}, simulation_id={simulation_id}, job_id={job_id}"

    try:
        # ── TX1: Job → PROCESSING ──────────────────────────────
        await _tx1_processing(job_id)

        # ── Y-Report 생성 (트랜잭션 외부) ─────────────────────
        from app.services.y_report_service import YReportService

        result = await YReportService().run(
            simulation_id=simulation_id,
            x_report_id=x_report_id,
            options=options or {},
        )

        output_json: Dict[str, Any] = {
            "structured": result["output"].get("structured", {}),
            "adapter_meta": result["output"].get("meta", {}),
            "service_meta": result["meta"],
        }
        markdown: str = result["output"].get("markdown", "")

        # ── TX2: YReport + Job → COMPLETED ─────────────────────
        await _tx2_completed(job_id, y_report_id, output_json, markdown)

        timing_ms = result["meta"].get("timing_ms", "?")
        print(f"[YReportTask] 완료: y_report_id={y_report_id}, timing_ms={timing_ms}ms")
        return {"y_report_id": y_report_id, "status": "completed"}

    except RuntimeError as e:
        error_msg = f"{str(e)[:550]} | Context: {context}"
        await _tx3_failed(job_id, y_report_id, error_msg)
        print(f"[YReportTask] 실패(RuntimeError): {error_msg[:200]}")
        raise

    except Exception as e:
        error_msg = _format_error(
            "UNKNOWN_ERROR",
            f"{type(e).__name__}: {str(e)}",
            context,
        )
        await _tx3_failed(job_id, y_report_id, error_msg)
        print(f"[YReportTask] 실패(Exception): {error_msg[:200]}")
        raise
