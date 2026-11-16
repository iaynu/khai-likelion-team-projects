import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.ingest import IngestRequest, IngestResponse
from app.services.ingest_service import run_ingest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/ingest", response_model=IngestResponse, status_code=201)
async def ingest(body: IngestRequest, db: AsyncSession = Depends(get_db)):
    """
    Ingest market_sales_review and/or population JSON payloads.
    Validates via jsonschema, upserts stores + documents under a snapshot version.
    """
    result = await run_ingest(
        session=db,
        snapshot_version=body.snapshot_version,
        market_sales_review=body.market_sales_review,
        population=body.population,
    )

    if result.get("errors"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "errors": result["errors"],
            },
        )

    await db.commit()

    return IngestResponse(
        snapshot_version=result["snapshot_version"],
        stores_inserted=result["stores_inserted"],
        stores_updated=result["stores_updated"],
        documents_inserted=result["documents_inserted"],
        documents_updated=result["documents_updated"],
    )
