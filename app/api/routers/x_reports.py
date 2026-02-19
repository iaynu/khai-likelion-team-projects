import logging

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.config import settings
from app.schemas.x_report import XReportCreateRequest, XReportSaveRequest, XReportOut
from app.schemas.strategy import StrategyListOut, StrategyOut
from app.services.x_report_service import create_x_report
from app.repositories.x_report_repo import XReportRepo
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.snapshot_repo import SnapshotRepo
from app.repositories.job_repo import JobRepo
from app.models.x_report import XReport
from app.models.strategy import Strategy
from app.models.job import JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/x-reports", tags=["x-reports"])


# ── API Key 검증 ─────────────────────────────────────
def verify_worker_api_key(x_api_key: str = Header(...)):
    """Worker API Key 검증"""
    if x_api_key != settings.WORKER_API_KEY:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid API Key"})


@router.post("", response_model=XReportOut, status_code=201)
async def generate_x_report(body: XReportCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    Generate (or retrieve cached) X-Report for a store+snapshot+prompt combination.
    Atomically creates x_report row + 12 strategy rows.
    """
    if not body.store_source_id and not body.store_name:
        raise HTTPException(
            status_code=422,
            detail={"code": "MISSING_STORE_IDENTIFIER", "message": "store_source_id 또는 store_name 중 하나 필수"},
        )

    try:
        result = await create_x_report(
            session=db,
            store_source_id=body.store_source_id,
            store_name=body.store_name,
            snapshot_version=body.snapshot_version,
            prompt_id=body.prompt_id,
            force_regenerate=body.force_regenerate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": str(exc)})

    report = result["report"]
    return XReportOut.model_validate(report, from_attributes=True)


@router.get("/{report_id}", response_model=XReportOut)
async def get_x_report(report_id: int, db: AsyncSession = Depends(get_db)):
    repo = XReportRepo(db)
    report = await repo.get_by_id(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"code": "XREPORT_NOT_FOUND", "message": f"id={report_id}"})
    return XReportOut.model_validate(report, from_attributes=True)


@router.get("/{report_id}/strategies", response_model=StrategyListOut)
async def get_strategies(report_id: int, db: AsyncSession = Depends(get_db)):
    report_repo = XReportRepo(db)
    report = await report_repo.get_by_id(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"code": "XREPORT_NOT_FOUND", "message": f"id={report_id}"})

    strat_repo = StrategyRepo(db)
    items = await strat_repo.list_by_report(report_id)
    total = await strat_repo.count_by_report(report_id)
    return StrategyListOut(
        items=[StrategyOut.model_validate(s, from_attributes=True) for s in items],
        total=total,
    )


@router.post("/save", response_model=XReportOut, status_code=201, dependencies=[Depends(verify_worker_api_key)])
async def save_x_report(body: XReportSaveRequest, db: AsyncSession = Depends(get_db)):
    """
    Worker가 생성한 X-Report 저장 (Worker 전용 API)

    Worker가 LLM으로 생성한 X-Report와 12개 Strategies를 저장합니다.
    API Key 인증 필요: Header에 X-API-Key 포함

    작업 흐름:
    1. Job 상태 확인
    2. Store 조회
    3. Snapshot 조회/생성
    4. X-Report 생성
    5. 12개 Strategies 생성
    6. Job 상태를 COMPLETED로 업데이트
    """
    job_repo = JobRepo(db)
    store_repo = StoreRepo(db)
    snapshot_repo = SnapshotRepo(db)
    xreport_repo = XReportRepo(db)

    # 1. Job 확인
    job = await job_repo.get_by_id(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": f"job_id={body.job_id}"})

    if job.status != JobStatus.PROCESSING:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_JOB_STATUS", "message": f"Job must be in PROCESSING status, got {job.status}"}
        )

    # 2. Store 조회
    store = await store_repo.get_by_source_id(body.store_source_id)
    if not store:
        raise HTTPException(
            status_code=404,
            detail={"code": "STORE_NOT_FOUND", "message": f"store_source_id={body.store_source_id}"}
        )

    # 3. Snapshot 조회/생성
    snapshot = await snapshot_repo.get_by_version(body.snapshot_version)
    if not snapshot:
        snapshot = await snapshot_repo.create(body.snapshot_version)
        await db.flush()

    # 4. X-Report 생성
    xreport = XReport(
        store_id=store.id,
        snapshot_id=snapshot.id,
        prompt_id=body.prompt_id,
        status="completed",
        content_text=body.content_text,
        config_json=body.config_json,
        model_meta=body.model_meta,
    )
    db.add(xreport)
    await db.flush()
    await db.refresh(xreport)

    # 5. Strategies 생성
    for strat_data in body.strategies:
        strategy = Strategy(
            x_report_id=xreport.id,
            solution_group=strat_data.solution_group,
            action_idx=strat_data.action_idx,
            title=strat_data.title,
            description=strat_data.description,
            config=strat_data.config,
            impact_mapping=strat_data.impact_mapping,
        )
        db.add(strategy)

    await db.flush()

    # 6. Job 상태 업데이트
    await job_repo.update_status(
        job_id=body.job_id,
        status=JobStatus.COMPLETED,
        result_id=xreport.id,
        progress=100,
    )

    await db.commit()
    await db.refresh(xreport)

    logger.info(f"X-Report saved: id={xreport.id}, job_id={body.job_id}, strategies={len(body.strategies)}")
    return XReportOut.model_validate(xreport, from_attributes=True)
