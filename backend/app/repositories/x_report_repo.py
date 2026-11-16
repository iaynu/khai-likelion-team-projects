from datetime import datetime, timezone
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

    async def create_completed(
        self,
        store_id: str,
        snapshot_id: int,
        prompt_id: str,
        content_text: str,
        config_json: Optional[dict] = None,
        model_meta: Optional[dict] = None,
    ) -> XReport:
        """
        Create a completed XReport with full content.

        Args:
            store_id: Store ID
            snapshot_id: Snapshot ID
            prompt_id: Prompt ID
            content_text: Full GPT-generated text
            config_json: Optional config metadata
            model_meta: Optional model metadata (model name, tokens, etc.)

        Returns:
            Created XReport object
        """
        report = XReport(
            store_id=store_id,
            snapshot_id=snapshot_id,
            prompt_id=prompt_id,
            status="completed",
            content_text=content_text,
            config_json=config_json,
            model_meta=model_meta,
        )
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def get_by_unique_key(
        self, store_id: str, snapshot_id: int, prompt_id: str
    ) -> Optional[XReport]:
        """UniqueConstraint(store_id, snapshot_id, prompt_id) 기준으로 기존 행 조회.
        상태 필터 없음 — SUCCEEDED/FAILED/RUNNING 모두 반환.
        """
        result = await self.session.execute(
            select(XReport).where(
                XReport.store_id == store_id,
                XReport.snapshot_id == snapshot_id,
                XReport.prompt_id == prompt_id,
            )
        )
        return result.scalar_one_or_none()

    async def update_report_fields(
        self,
        x_report_id: int,
        content_text: str,
        config_json: Optional[dict] = None,
        model_meta: Optional[dict] = None,
    ) -> XReport:
        """기존 XReport 행을 SUCCEEDED 상태로 덮어쓴다.
        strategies는 별도로 replace_all()을 통해 교체해야 한다.
        """
        report = await self.get_by_id(x_report_id)
        if not report:
            raise ValueError(f"XReport not found: id={x_report_id}")
        report.content_text = content_text
        report.config_json = config_json
        report.model_meta = model_meta
        report.status = "SUCCEEDED"
        report.finished_at = datetime.now(timezone.utc)
        report.error_message = None
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def get_merged_input(self, store_id: str, snapshot_id: int) -> dict:
        """Calls fn_get_merged_input Postgres function. Falls back gracefully on SQLite."""
        result = await self.session.execute(
            text("SELECT fn_get_merged_input(:store_id, :snapshot_id)"),
            {"store_id": store_id, "snapshot_id": snapshot_id},
        )
        row = result.fetchone()
        return row[0] if row else {}
