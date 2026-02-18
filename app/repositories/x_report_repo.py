from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.x_report import XReport


class XReportRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, report_id: int) -> Optional[XReport]:
        result = await self.session.execute(
            select(XReport).where(XReport.id == report_id)
        )
        return result.scalar_one_or_none()

    async def get_cached(self, store_id: str, snapshot_id: int, prompt_id: str) -> Optional[XReport]:
        result = await self.session.execute(
            select(XReport).where(
                XReport.store_id == store_id,
                XReport.snapshot_id == snapshot_id,
                XReport.prompt_id == prompt_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_pending(self, store_id: str, snapshot_id: int, prompt_id: str) -> XReport:
        report = XReport(
            store_id=store_id,
            snapshot_id=snapshot_id,
            prompt_id=prompt_id,
            status="PENDING",
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def get_merged_input(self, store_id: str, snapshot_id: int) -> dict:
        """Calls fn_get_merged_input Postgres function. Falls back gracefully on SQLite."""
        result = await self.session.execute(
            text("SELECT fn_get_merged_input(:store_id, :snapshot_id)"),
            {"store_id": store_id, "snapshot_id": snapshot_id},
        )
        row = result.fetchone()
        return row[0] if row else {}
