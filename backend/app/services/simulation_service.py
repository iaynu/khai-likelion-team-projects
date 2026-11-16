"""
SimulationService — 시뮬레이션 실행 오케스트레이션

Task에서 Adapter를 직접 호출하지 않고 Service를 경유함으로써:
  - 에러 분류 (BACKEND_TIMEOUT / UNKNOWN_ERROR) 을 한 곳에서 처리
  - run_id / timing_ms 등 관측 가능성(observability) meta 기록
  - JSON-serializable output 보장 (to_jsonable 적용)
  - Adapter 교체/확장 시 Task 코드를 건드리지 않아도 됨

model_meta 관련 설계 결정:
  시뮬레이션 엔진은 내부적으로 LLM(GPT)을 호출하지만,
  현재 run_simulation() / run_full_pipeline() API가 token 수·request_id를
  외부로 반환하지 않는다. 따라서 llm_tokens 필드는 None으로 남겨두고,
  엔진이 해당 정보를 노출하면 meta에 추가한다.
  그 외 관측 가능한 meta (engine, run_id, timing_ms, agent_count, days)는
  항상 기록한다.
"""
import asyncio
import time
import uuid
from typing import Any, Dict, List

from app.utils.serialization import to_jsonable

# 에러 코드 상수
ERROR_TIMEOUT = "BACKEND_TIMEOUT"
ERROR_UNKNOWN = "UNKNOWN_ERROR"


class SimulationService:
    """
    시뮬레이션 전체 파이프라인(before + strategy bridge + after) 실행 서비스.

    Celery Task에서 asyncio.run(SimulationService().run(...)) 형태로 호출한다.
    세션 주입 없이 독립 실행 가능 (엔진 I/O는 파일 기반).
    """

    async def run(
        self,
        *,
        store_source_id: str,
        selected_strategy_ids: List[str],
        days: int,
        seed: int,
        agent_count: int,
        max_concurrent_llm_calls: int = 5,
        api_key: str = "",
    ) -> Dict[str, Any]:
        """
        시뮬레이션 파이프라인을 실행하고 JSON-serializable 결과를 반환한다.

        Args:
            store_source_id: 타겟 매장명 (예: "돼지야")
            selected_strategy_ids: 전략 ID 목록. 빈 리스트이면 before-only 실행
            days: 시뮬레이션 기간 (일)
            seed: 랜덤 시드 (before/after 에이전트 재현성 보장)
            agent_count: 에이전트 수
            max_concurrent_llm_calls: 동시 LLM 호출 수 상한
            api_key: LLM API 키 (전략 적용 시 필요)

        Returns:
            {
                "output": {
                    "target_store": str,
                    "before": { visit_count, avg_rating, target_visit_count,
                                top10_stores, output_dir },
                    "after":  { ... } | None,
                },
                "meta": {
                    "engine":                   "run_full_pipeline",
                    "run_id":                   str (UUID4),
                    "timing_ms":                int,
                    "agent_count":              int,
                    "days":                     int,
                    "max_concurrent_llm_calls": int,
                    "llm_tokens":               None,  # 엔진 미지원
                },
            }

        Raises:
            RuntimeError: "[BACKEND_TIMEOUT] ..." 또는 "[UNKNOWN_ERROR] ..."
                          호출부(Task)에서 str(e) 로 error_message를 추출한다.
        """
        from app.adapters.simulation_adapter import run_full_pipeline

        run_id = str(uuid.uuid4())
        t0 = time.perf_counter()

        try:
            raw_output = await run_full_pipeline(
                store_source_id=store_source_id,
                selected_strategy_ids=selected_strategy_ids,
                days=days,
                seed=seed,
                agent_count=agent_count,
                max_concurrent_llm_calls=max_concurrent_llm_calls,
                api_key=api_key,
            )
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"[{ERROR_TIMEOUT}] 시뮬레이션 시간 초과 | "
                f"Context: store={store_source_id}, days={days}, "
                f"agent_count={agent_count}"
            ) from e
        except Exception as e:
            raise RuntimeError(
                f"[{ERROR_UNKNOWN}] {type(e).__name__}: {str(e)[:300]} | "
                f"Context: store={store_source_id}, days={days}"
            ) from e

        timing_ms = round((time.perf_counter() - t0) * 1000)

        meta: Dict[str, Any] = {
            "engine": "run_full_pipeline",
            "run_id": run_id,
            "timing_ms": timing_ms,
            "agent_count": agent_count,
            "days": days,
            "max_concurrent_llm_calls": max_concurrent_llm_calls,
            # 엔진 내부 LLM 호출 정보: 현재 엔진 API가 외부로 노출하지 않음
            "llm_tokens": None,
        }

        return {
            "output": to_jsonable(raw_output),
            "meta": to_jsonable(meta),
        }
