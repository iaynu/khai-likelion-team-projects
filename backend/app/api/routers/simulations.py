"""
Simulations Router

POST /simulations                           — 시뮬레이션 Job 생성 + asyncio.create_task enqueue
GET  /simulations/{sim_id}                 — 단건 결과 조회
GET  /stores/{store_source_id}/simulations — 매장별 이력 목록
"""
import asyncio
from fastapi import APIRouter, HTTPException, Query

from app.db.session import get_async_session
from app.repositories.job_repo import JobRepo
from app.repositories.simulation_repo import SimulationRepo
from app.models.job import JobType
from app.schemas.simulation import (
    SimulationCreate,
    SimulationJobResponse,
    SimulationListResponse,
    SimulationResponse,
)

router = APIRouter(prefix="/simulations", tags=["simulations"])
store_router = APIRouter(prefix="/stores", tags=["simulations"])

# Strong references to background tasks to prevent GC before completion
_background_tasks: set = set()

def _fire_and_forget(coro) -> asyncio.Task:
    """Schedule coro as a background task and keep a strong reference."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@router.post(
    "",
    response_model=SimulationJobResponse,
    status_code=201,
    summary="시뮬레이션 실행 요청",
    responses={
        201: {"description": "Job 생성 성공, asyncio.create_task enqueue 완료"},
        422: {"description": "요청 파라미터 검증 실패"},
    },
)
async def create_simulation(body: SimulationCreate):
    """
    시뮬레이션 Job을 생성하고 백그라운드 태스크를 실행합니다.

    **흐름:**
    1. `simulations` 테이블에 PENDING 레코드 생성
    2. `jobs` 테이블에 PENDING 레코드 생성
    3. `run_simulation` asyncio.create_task 로 백그라운드 실행

    **응답:** `job_id`와 `sim_id`를 반환합니다.
    프론트엔드는 `GET /jobs/{job_id}`로 폴링하여 진행 상태를 확인하세요.

    **결과 조회:** 완료 후 `GET /simulations/{sim_id}`로 결과를 조회할 수 있습니다.
    """
    from tasks.simulation_task import run_simulation

    input_json = body.model_dump()

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id=body.store_source_id,
                x_report_id=body.x_report_id,
                selected_strategy_ids=body.selected_strategy_ids,
                days=body.days,
                seed=body.seed,
                input_json=input_json,
                settings_json={"agent_count": body.agent_count},
            )

            job = await JobRepo(session).create(
                job_type=JobType.SIMULATION,
                store_source_id=body.store_source_id,
            )

    # asyncio.create_task 로 백그라운드 실행 (트랜잭션 외부)
    _fire_and_forget(run_simulation(
        job_id=job.id,
        sim_id=sim.id,
        store_source_id=body.store_source_id,
        selected_strategy_ids=body.selected_strategy_ids,
        days=body.days,
        seed=body.seed,
        agent_count=body.agent_count,
        max_concurrent_llm_calls=body.max_concurrent_llm_calls,
    ))

    return SimulationJobResponse(job_id=job.id, sim_id=sim.id)


@router.get(
    "/{sim_id}",
    response_model=SimulationResponse,
    summary="시뮬레이션 결과 조회",
    responses={
        200: {"description": "시뮬레이션 레코드 (status: PENDING/PROCESSING/COMPLETED/FAILED)"},
        404: {"description": "존재하지 않는 sim_id"},
    },
)
async def get_simulation(sim_id: int):
    """
    시뮬레이션 레코드를 조회합니다.

    **status 값:**
    - `PENDING` — Celery 태스크 대기 중
    - `PROCESSING` — 엔진 실행 중
    - `COMPLETED` — 완료. `output_json`과 `summary`에서 결과를 확인하세요
    - `FAILED` — 실패. `error_message`에서 에러 코드를 확인하세요

    **`summary` 필드** (COMPLETED일 때 자동 계산):
    - `timing_ms`: 엔진 실행 시간 (밀리초)
    - `agent_count`: 사용된 에이전트 수
    - `days`: 시뮬레이션 기간
    - `engine`: 사용된 엔진명
    - `run_id`: 실행 UUID
    """
    async with get_async_session() as session:
        sim = await SimulationRepo(session).get_by_id(sim_id)
        if not sim:
            raise HTTPException(status_code=404, detail=f"Simulation {sim_id} 없음")
        return SimulationResponse.model_validate(sim)


@store_router.get(
    "/{store_source_id}/simulations",
    response_model=SimulationListResponse,
    summary="매장별 시뮬레이션 이력 조회",
    responses={
        200: {"description": "최신순 시뮬레이션 목록"},
    },
)
async def list_store_simulations(
    store_source_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="최대 반환 개수 (1–100)"),
):
    """
    특정 매장의 시뮬레이션 실행 이력을 최신순으로 반환합니다.

    **경로 파라미터:**
    - `store_source_id`: 매장명 (예: `돼지야`)

    **쿼리 파라미터:**
    - `limit`: 최대 반환 개수 (1–100, 기본 20)
    """
    async with get_async_session() as session:
        sims = await SimulationRepo(session).list_by_store(
            store_source_id=store_source_id, limit=limit
        )
    items = [SimulationResponse.model_validate(s) for s in sims]
    return SimulationListResponse(items=items, total=len(items))
