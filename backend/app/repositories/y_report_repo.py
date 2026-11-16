"""
YReport Repository - DB 접근

인터페이스 설계 (XReportRepo 패턴 준수):
──────────────────────────────────────────────────────────────
 create_pending(...)   → PENDING 레코드만 생성.
                          Celery task enqueue 직전 Router에서 호출.
                          Job 생성과 같은 TX 안에 묶는다.

 create_completed(...) → 생성 + COMPLETED 전환을 원자적으로 처리.
                          동기식 실행 / 통합 테스트 시나리오용.
                          (일반 Celery 흐름에서는 사용하지 않는다)

 get_by_id(report_id) → PK 조회.

 update_completed(...) → COMPLETED 전환. TX2에서 호출.
                          Job 상태 업데이트는 JobRepo.update_status()로 별도 처리한다.

 update_failed(...)   → FAILED 전환. TX3에서 호출.
                          Job 상태 업데이트는 JobRepo.update_status()로 별도 처리한다.

 list_by_simulation(...)  → Simulation 기준 보고서 이력 조회.
──────────────────────────────────────────────────────────────
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.y_report import YReport


class YReportRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 생성 ────────────────────────────────────────────────

    async def create_pending(
        self,
        store_source_id: str,
        simulation_id: int,
        x_report_id: Optional[int] = None,
        input_json: Optional[Dict] = None,
    ) -> YReport:
        """
        PENDING 상태의 YReport 레코드를 생성한다.

        Celery task enqueue 직전 Router에서 호출. Job 생성과 같은 TX 안에 묶는다.
        사전 조건: simulation_id에 해당하는 Simulation이 COMPLETED 상태여야 한다
        (Router에서 검증 후 호출).

        Args:
            store_source_id: 대상 매장 ID
            simulation_id: 연결된 Simulation ID (before/after 경로 조회용)
            x_report_id: 연결된 X-Report ID (선택)
            input_json: 요청 파라미터 원본 (선택)

        Returns:
            status="PENDING"인 YReport 인스턴스
        """
        report = YReport(
            store_source_id=store_source_id,
            simulation_id=simulation_id,
            x_report_id=x_report_id,
            input_json=input_json,
            status="PENDING",
        )
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def create_completed(
        self,
        store_source_id: str,
        simulation_id: int,
        output_json: Dict[str, Any],
        markdown: str,
        x_report_id: Optional[int] = None,
        input_json: Optional[Dict] = None,
    ) -> YReport:
        """
        생성 + COMPLETED 전환을 원자적으로 처리한다.

        동기식 일괄 처리 / 통합 테스트 / 검증 스크립트 등
        Celery를 거치지 않고 즉시 완료 상태로 저장할 때 사용한다.
        일반 Celery 흐름에서는 create_pending → update_completed 순서를 따른다.

        Args:
            store_source_id: 대상 매장 ID
            simulation_id: 연결된 Simulation ID
            output_json: 분석 결과 지표 (JSON-serializable dict)
            markdown: 마크다운 형식의 보고서 본문
            x_report_id: 연결된 X-Report ID (선택)
            input_json: 요청 파라미터 원본 (선택)

        Returns:
            status="COMPLETED"인 YReport 인스턴스
        """
        report = YReport(
            store_source_id=store_source_id,
            simulation_id=simulation_id,
            x_report_id=x_report_id,
            input_json=input_json,
            output_json=output_json,
            markdown=markdown,
            status="COMPLETED",
            finished_at=datetime.utcnow(),
        )
        self.session.add(report)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    # ── 조회 ────────────────────────────────────────────────

    async def get_by_id(self, report_id: int) -> Optional[YReport]:
        """PK로 YReport를 조회한다."""
        stmt = select(YReport).where(YReport.id == report_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_simulation(
        self, simulation_id: int
    ) -> List[YReport]:
        """Simulation ID 기준으로 YReport 이력을 최신순으로 조회한다."""
        stmt = (
            select(YReport)
            .where(YReport.simulation_id == simulation_id)
            .order_by(YReport.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_store(
        self, store_source_id: str, limit: int = 20
    ) -> List[YReport]:
        """매장 ID 기준으로 YReport 이력을 최신순으로 조회한다."""
        stmt = (
            select(YReport)
            .where(YReport.store_source_id == store_source_id)
            .order_by(YReport.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ── 상태 전환 (Celery TX2 / TX3) ────────────────────────

    async def update_completed(
        self,
        report_id: int,
        output_json: Dict[str, Any],
        markdown: str,
    ) -> Optional[YReport]:
        """
        YReport를 COMPLETED로 전환하고 결과를 저장한다. [TX2 전용]

        Job 상태 업데이트는 이 메서드의 책임이 아니다.
        호출부(task)에서 같은 TX 안에 JobRepo.update_status()를 함께 호출해야 한다.

        Args:
            report_id: 업데이트할 YReport ID
            output_json: 분석 결과 지표 (JSON-serializable dict)
            markdown: 마크다운 형식의 보고서 본문

        Returns:
            업데이트된 YReport, 또는 None (report_id 미존재 시)
        """
        report = await self.get_by_id(report_id)
        if not report:
            return None
        report.status = "COMPLETED"
        report.output_json = output_json
        report.markdown = markdown
        report.finished_at = datetime.utcnow()
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def update_failed(
        self, report_id: int, error_message: str
    ) -> Optional[YReport]:
        """
        YReport를 FAILED로 전환하고 에러 메시지를 기록한다. [TX3 전용]

        Job 상태 업데이트는 이 메서드의 책임이 아니다.
        호출부(task)에서 같은 TX 안에 JobRepo.update_status()를 함께 호출해야 한다.

        Args:
            report_id: 업데이트할 YReport ID
            error_message: 에러 설명 ([ERROR_CODE] message | Context: ... 형식 권장)

        Returns:
            업데이트된 YReport, 또는 None (report_id 미존재 시)
        """
        report = await self.get_by_id(report_id)
        if not report:
            return None
        report.status = "FAILED"
        report.error_message = error_message
        report.finished_at = datetime.utcnow()
        await self.session.flush()
        await self.session.refresh(report)
        return report
