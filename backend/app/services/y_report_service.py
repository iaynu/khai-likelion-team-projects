"""
YReportService — Y-Report 생성 오케스트레이션

SimulationService와 같은 계층 패턴:
  1. Simulation DB 레코드에서 output_json (before/after 경로) 로드
  2. XReport 정보 로드 (x_report_id 제공 시 — 현재는 meta용 컨텍스트)
  3. YReportAdapter.generate() 호출 (asyncio.to_thread 내부 처리)
  4. run_id / timing_ms 기록
  5. 에러 분류 → [INVALID_INPUT] / [MISSING_DATA] / [BACKEND_TIMEOUT] / [UNKNOWN_ERROR]

에러 코드 정책:
  [INVALID_INPUT]   — Simulation 미존재·COMPLETED 아님·dirs 없음 등 검증 실패
  [MISSING_DATA]    — before_dir / after_dir 파일시스템 경로 없음 (FileNotFoundError)
  [BACKEND_TIMEOUT] — asyncio.TimeoutError
  [UNKNOWN_ERROR]   — 그 외 모든 예외
"""
import asyncio
import time
import uuid
from typing import Any, Dict, Optional

from app.utils.serialization import to_jsonable

# 에러 코드 상수
ERROR_INVALID_INPUT = "INVALID_INPUT"
ERROR_MISSING_DATA = "MISSING_DATA"
ERROR_TIMEOUT = "BACKEND_TIMEOUT"
ERROR_UNKNOWN = "UNKNOWN_ERROR"


class YReportService:
    """
    Y-Report 생성을 담당하는 서비스 클래스.

    Celery Task에서 asyncio.run(YReportService().run(...)) 형태로 호출한다.
    DB 세션은 내부에서 get_async_session()으로 직접 획득한다.
    """

    async def run(
        self,
        *,
        simulation_id: int,
        x_report_id: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Y-Report 생성 파이프라인을 실행하고 JSON-serializable 결과를 반환한다.

        Args:
            simulation_id: 완료된 Simulation ID. output_json에서 before/after 경로를 추출한다.
            x_report_id: 연결된 X-Report ID (선택). meta 컨텍스트용.
                         ComparisonReportGenerator는 x_report 데이터를 직접 사용하지 않는다.
            options: YReportAdapter에 전달할 추가 옵션
                - before_dir:   simulation output_json의 before.output_dir 오버라이드
                - after_dir:    simulation output_json의 after.output_dir 오버라이드
                - target_store: 매장명 오버라이드
                - output_dir:   보고서 저장 경로

        Returns:
            {
                "output": {
                    "structured": dict,   # metrics_summary (수치 지표)
                    "markdown":   str,    # 전체 마크다운 보고서
                    "meta": {             # 어댑터 레벨 메타
                        "target_store":  str,
                        "before_dir":    str,
                        "after_dir":     str,
                        "report_path":   str,
                        "x_report_keys": list,
                    },
                },
                "meta": {                 # 서비스 레벨 메타 (관측 가능성)
                    "engine":         "YReportAdapter",
                    "run_id":         str (UUID4),
                    "timing_ms":      int,
                    "simulation_id":  int,
                    "x_report_id":    int | None,
                },
            }

        Raises:
            RuntimeError: "[INVALID_INPUT]..." / "[MISSING_DATA]..." /
                          "[BACKEND_TIMEOUT]..." / "[UNKNOWN_ERROR]..."
                          호출부(Task)에서 str(e)로 error_message를 추출한다.
        """
        from app.db.session import get_async_session
        from app.repositories.simulation_repo import SimulationRepo
        from app.repositories.x_report_repo import XReportRepo
        from app.adapters.y_report_adapter import YReportAdapter

        if options is None:
            options = {}

        ctx = f"simulation_id={simulation_id}"

        # ── 1. Simulation 로드 및 검증 ───────────────────────────
        async with get_async_session() as session:
            sim = await SimulationRepo(session).get_by_id(simulation_id)

        if not sim:
            raise RuntimeError(
                f"[{ERROR_INVALID_INPUT}] Simulation {simulation_id} 없음 | Context: {ctx}"
            )
        if sim.status != "COMPLETED":
            raise RuntimeError(
                f"[{ERROR_INVALID_INPUT}] Simulation {simulation_id} 상태가 "
                f"COMPLETED가 아님: {sim.status} | Context: {ctx}"
            )

        simulation_output: Dict[str, Any] = sim.output_json or {}

        # before/after dirs 사전 검증 (options 오버라이드 고려)
        before_dir = options.get("before_dir") or simulation_output.get("before", {}).get("output_dir", "")
        after_dir = options.get("after_dir") or simulation_output.get("after", {}).get("output_dir", "")
        if not before_dir or not after_dir:
            raise RuntimeError(
                f"[{ERROR_INVALID_INPUT}] simulation output_json에 before/after output_dir 없음 | "
                f"Context: {ctx}"
            )

        # ── 2. XReport 로드 (선택, meta 컨텍스트용) ─────────────
        x_report: Dict[str, Any] = {}
        if x_report_id:
            async with get_async_session() as session:
                xr = await XReportRepo(session).get_by_id(x_report_id)
            if xr:
                x_report = {
                    "id": xr.id,
                    "store_id": xr.store_id,
                    "status": xr.status,
                }

        # ── 3. YReportAdapter 호출 ────────────────────────────────
        run_id = str(uuid.uuid4())
        t0 = time.perf_counter()

        try:
            adapter_result = await YReportAdapter().generate(
                simulation_output=simulation_output,
                x_report=x_report,
                options=options,
            )
        except (ValueError, KeyError) as e:
            raise RuntimeError(
                f"[{ERROR_INVALID_INPUT}] {type(e).__name__}: {str(e)[:300]} | Context: {ctx}"
            ) from e
        except FileNotFoundError as e:
            raise RuntimeError(
                f"[{ERROR_MISSING_DATA}] 출력 디렉토리 없음: {str(e)[:300]} | Context: {ctx}"
            ) from e
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"[{ERROR_TIMEOUT}] Y-Report 생성 시간 초과 | Context: {ctx}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"[{ERROR_UNKNOWN}] {type(e).__name__}: {str(e)[:300]} | Context: {ctx}"
            ) from e

        timing_ms = round((time.perf_counter() - t0) * 1000)

        service_meta: Dict[str, Any] = {
            "engine": "YReportAdapter",
            "run_id": run_id,
            "timing_ms": timing_ms,
            "simulation_id": simulation_id,
            "x_report_id": x_report_id,
        }

        return {
            "output": to_jsonable(adapter_result),
            "meta": to_jsonable(service_meta),
        }
