import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.x_report import XReportCreateRequest, XReportOut
from app.schemas.strategy import StrategyListOut, StrategyOut
from app.services.x_report_service import create_x_report
from app.repositories.x_report_repo import XReportRepo
from app.repositories.strategy_repo import StrategyRepo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/x-reports", tags=["x-reports"])


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
