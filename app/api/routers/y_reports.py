"""
Y-Reports Router

POST /y-reports                           — Y-Report Job 생성 + Celery enqueue
GET  /y-reports/{y_report_id}             — 단건 결과 조회
GET  /stores/{store_source_id}/y-reports  — 매장별 이력 목록
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_session, get_db
from app.repositories.job_repo import JobRepo
from app.repositories.simulation_repo import SimulationRepo
from app.repositories.y_report_repo import YReportRepo
from app.models.job import JobType
from app.schemas.y_report import (
    YReportCreate,
    YReportJobResponse,
    YReportListResponse,
    YReportResponse,
    YReportViewOut,
)
from app.services.y_report_view_service import build_y_report_view

router = APIRouter(prefix="/y-reports", tags=["y-reports"])
store_router = APIRouter(prefix="/stores", tags=["y-reports"])


@router.post(
    "",
    response_model=YReportJobResponse,
    status_code=201,
    summary="Y-Report 생성 요청",
    responses={
        201: {"description": "Job 생성 성공, Celery 태스크 enqueue 완료"},
        404: {"description": "simulation_id에 해당하는 Simulation 없음"},
        422: {"description": "Simulation이 COMPLETED 상태가 아니거나 파라미터 검증 실패"},
    },
)
async def create_y_report(body: YReportCreate):
    """
    Y-Report Job을 생성하고 Celery 태스크를 enqueue합니다.

    **사전 조건:** `simulation_id`에 해당하는 Simulation이 `COMPLETED` 상태여야 합니다.
    Simulation이 없거나 아직 실행 중이면 오류를 반환합니다.

    **흐름:**
    1. Simulation 존재 여부 및 `COMPLETED` 상태 확인
    2. `y_reports` 테이블에 PENDING 레코드 생성
    3. `jobs` 테이블에 PENDING 레코드 생성
    4. `tasks.generate_y_report` Celery 태스크 enqueue
    5. `job.celery_task_id` 저장

    **응답:** `job_id`와 `y_report_id`를 반환합니다.
    프론트엔드는 `GET /jobs/{job_id}`로 폴링하여 진행 상태를 확인하세요.

    **결과 조회:** 완료 후 `GET /y-reports/{y_report_id}`로 결과를 조회할 수 있습니다.
    """
    from tasks.y_report_task import generate_y_report as celery_generate_y_report

    # Simulation 상태 확인
    async with get_async_session() as session:
        sim = await SimulationRepo(session).get_by_id(body.simulation_id)
        if not sim:
            raise HTTPException(
                status_code=404,
                detail=f"Simulation {body.simulation_id} 없음",
            )
        if sim.status != "COMPLETED":
            raise HTTPException(
                status_code=422,
                detail=f"Simulation {body.simulation_id} 상태가 COMPLETED가 아님: {sim.status}",
            )
        store_source_id = sim.store_source_id

    async with get_async_session() as session:
        async with session.begin():
            y_report = await YReportRepo(session).create_pending(
                store_source_id=store_source_id,
                simulation_id=body.simulation_id,
                x_report_id=body.x_report_id,
                input_json=body.model_dump(),
            )

            job = await JobRepo(session).create(
                job_type=JobType.Y_REPORT,
                store_source_id=store_source_id,
            )

    # Celery enqueue (트랜잭션 외부)
    task = celery_generate_y_report.delay(
        job_id=job.id,
        y_report_id=y_report.id,
        simulation_id=body.simulation_id,
        x_report_id=body.x_report_id,
        options=body.options or {},
    )

    # celery_task_id 저장
    async with get_async_session() as session:
        async with session.begin():
            db_job = await JobRepo(session).get_by_id(job.id)
            if db_job:
                db_job.celery_task_id = task.id

    return YReportJobResponse(job_id=job.id, y_report_id=y_report.id)


@router.get(
    "/{y_report_id}",
    response_model=YReportResponse,
    summary="Y-Report 결과 조회",
    responses={
        200: {"description": "Y-Report 레코드 (status: PENDING/PROCESSING/COMPLETED/FAILED)"},
        404: {"description": "존재하지 않는 y_report_id"},
    },
)
async def get_y_report(y_report_id: int):
    """
    Y-Report 레코드를 조회합니다.

    **status 값:**
    - `PENDING` — Celery 태스크 대기 중
    - `PROCESSING` — 비교 분석 실행 중
    - `COMPLETED` — 완료. `output_json`, `markdown`, `summary`에서 결과를 확인하세요
    - `FAILED` — 실패. `error_message`에서 에러 코드를 확인하세요

    **`summary` 필드** (COMPLETED일 때 자동 계산):
    - `timing_ms`: 엔진 실행 시간 (밀리초)
    - `engine`: 사용된 엔진명 (`YReportAdapter`)
    - `run_id`: 실행 UUID
    - `total_visits_before` / `total_visits_after` / `visit_change_pct`: 핵심 KPI (엔진 출력에 따라 가변)

    **`markdown` 필드:** 전략 전/후 비교 분석 보고서 전문 (마크다운 형식).
    """
    async with get_async_session() as session:
        report = await YReportRepo(session).get_by_id(y_report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"YReport {y_report_id} 없음")
        return YReportResponse.model_validate(report)


@router.get(
    "/{y_report_id}/view",
    response_model=YReportViewOut,
    summary="Y-Report View DTO 조회 (프론트 전용)",
    responses={
        200: {"description": "Y-Report View DTO — overview/keywords/hourlyTraffic/retention/sideEffects/riskScore/llmSummary"},
        404: {"description": "존재하지 않는 y_report_id"},
        500: {"description": "output_json 파싱 실패 및 markdown 없음 [YREPORT_PARSE_ERROR]"},
    },
)
async def get_y_report_view(
    y_report_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Y-Report View DTO — 프론트엔드 전용 가공 데이터.

    - `overview`: sim1(before)/sim2(after) 방문수 + 시장점유율
    - `keywords`: sim1/sim2 키워드 클라우드
    - `hourlyTraffic`: 시간대별 트래픽 비교
    - `retention`: 재방문율 지표
    - `sideEffects`: 역효과 목록
    - `riskScore`: 솔루션 안전성 점수
    - `llmSummary`: LLM 종합 평가 (마크다운)

    sim1 = 기준(before), sim2 = 전략 적용(after).
    데이터 없는 필드는 [] / 기본 구조로 반환 (null 금지).

    **에러:**
    - `404`: y_report_id 없음
    - `500 [YREPORT_PARSE_ERROR]`: output_json 파싱 실패 + markdown 없음
    """
    try:
        view = await build_y_report_view(session=db, report_id=y_report_id)
    except ValueError as exc:
        err_msg = str(exc)
        if "[YREPORT_PARSE_ERROR]" in err_msg:
            raise HTTPException(
                status_code=500,
                detail={"code": "YREPORT_PARSE_ERROR", "message": err_msg},
            )
        raise HTTPException(status_code=500, detail={"code": "INTERNAL_ERROR", "message": err_msg})

    if view is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "YREPORT_NOT_FOUND", "message": f"id={y_report_id}"},
        )
    return view


@store_router.get(
    "/{store_source_id}/y-reports",
    response_model=YReportListResponse,
    summary="매장별 Y-Report 이력 조회",
    responses={
        200: {"description": "최신순 Y-Report 목록"},
    },
)
async def list_store_y_reports(
    store_source_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="최대 반환 개수 (1–100)"),
):
    """
    특정 매장의 Y-Report 생성 이력을 최신순으로 반환합니다.

    **경로 파라미터:**
    - `store_source_id`: 매장명 (예: `돼지야`)

    **쿼리 파라미터:**
    - `limit`: 최대 반환 개수 (1–100, 기본 20)
    """
    async with get_async_session() as session:
        reports = await YReportRepo(session).list_by_store(
            store_source_id=store_source_id, limit=limit
        )
    items = [YReportResponse.model_validate(r) for r in reports]
    return YReportListResponse(items=items, total=len(items))
