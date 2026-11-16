"""
Simulation Repository - DB 접근

인터페이스 설계 (XReportRepo 패턴 준수):
──────────────────────────────────────────────────────────────
 create_pending(...)   → PENDING 레코드만 생성.
                          Celery task를 enqueue하기 직전에 호출한다.
                          Router에서 Job과 함께 같은 TX 안에 생성한다.

 create_completed(...) → 생성 + COMPLETED 전환을 원자적으로 처리.
                          동기식 실행 / 통합 테스트 시나리오용.
                          (일반 Celery 흐름에서는 사용하지 않는다)

 get_by_id(sim_id)    → PK 조회.

 update_completed(...) → COMPLETED 전환. TX2에서 호출.
                          Job 상태 업데이트는 JobRepo.update_status()로 별도 처리한다.

 update_failed(...)   → FAILED 전환. TX3에서 호출.
                          Job 상태 업데이트는 JobRepo.update_status()로 별도 처리한다.

 list_by_store(...)   → 매장별 이력 조회.
──────────────────────────────────────────────────────────────
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.simulation import Simulation


class SimulationRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 생성 ────────────────────────────────────────────────

    async def create_pending(
        self,
        store_source_id: str,
        x_report_id: Optional[int],
        selected_strategy_ids: List[str],
        days: int,
        seed: int,
        input_json: Optional[Dict] = None,
        settings_json: Optional[Dict] = None,
    ) -> Simulation:
        """
        PENDING 상태의 Simulation 레코드를 생성한다.

        Celery task enqueue 직전 Router에서 호출. Job 생성과 같은 TX 안에 묶는다.

        Args:
            store_source_id: 대상 매장 ID
            x_report_id: 연결된 X-Report ID (없으면 None)
            selected_strategy_ids: 전략 ID 목록 (예: ["S1_A", "S2_B"])
            days: 시뮬레이션 기간 (일)
            seed: 랜덤 시드
            input_json: 요청 파라미터 원본 (선택)
            settings_json: 엔진 설정 (예: {"agent_count": 100}) (선택)

        Returns:
            status="PENDING"인 Simulation 인스턴스
        """
        sim = Simulation(
            store_source_id=store_source_id,
            x_report_id=x_report_id,
            selected_strategy_ids=selected_strategy_ids,
            days=days,
            seed=seed,
            input_json=input_json,
            settings_json=settings_json,
            status="PENDING",
        )
        self.session.add(sim)
        await self.session.flush()
        await self.session.refresh(sim)
        return sim

    async def create_completed(
        self,
        store_source_id: str,
        x_report_id: Optional[int],
        selected_strategy_ids: List[str],
        days: int,
        seed: int,
        output_json: Dict[str, Any],
        input_json: Optional[Dict] = None,
        settings_json: Optional[Dict] = None,
    ) -> Simulation:
        """
        생성 + COMPLETED 전환을 원자적으로 처리한다.

        동기식 일괄 처리 / 통합 테스트 / 검증 스크립트 등
        Celery를 거치지 않고 즉시 완료 상태로 저장할 때 사용한다.
        일반 Celery 흐름에서는 create_pending → update_completed 순서를 따른다.

        Args:
            store_source_id: 대상 매장 ID
            x_report_id: 연결된 X-Report ID (없으면 None)
            selected_strategy_ids: 전략 ID 목록
            days: 시뮬레이션 기간 (일)
            seed: 랜덤 시드
            output_json: 시뮬레이션 결과 (JSON-serializable dict)
            input_json: 요청 파라미터 원본 (선택)
            settings_json: 엔진 설정 (선택)

        Returns:
            status="COMPLETED"인 Simulation 인스턴스
        """
        sim = Simulation(
            store_source_id=store_source_id,
            x_report_id=x_report_id,
            selected_strategy_ids=selected_strategy_ids,
            days=days,
            seed=seed,
            input_json=input_json,
            settings_json=settings_json,
            output_json=output_json,
            status="COMPLETED",
            finished_at=datetime.utcnow(),
        )
        self.session.add(sim)
        await self.session.flush()
        await self.session.refresh(sim)
        return sim

    # ── 조회 ────────────────────────────────────────────────

    async def get_by_id(self, sim_id: int) -> Optional[Simulation]:
        """PK로 Simulation을 조회한다."""
        stmt = select(Simulation).where(Simulation.id == sim_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_store(
        self, store_source_id: str, limit: int = 20
    ) -> List[Simulation]:
        """매장 ID 기준으로 Simulation 이력을 최신순으로 조회한다."""
        stmt = (
            select(Simulation)
            .where(Simulation.store_source_id == store_source_id)
            .order_by(Simulation.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ── 상태 전환 (Celery TX2 / TX3) ────────────────────────

    async def update_completed(
        self, sim_id: int, output_json: Dict[str, Any]
    ) -> Optional[Simulation]:
        """
        Simulation을 COMPLETED로 전환하고 결과를 저장한다. [TX2 전용]

        Job 상태 업데이트는 이 메서드의 책임이 아니다.
        호출부(task)에서 같은 TX 안에 JobRepo.update_status()를 함께 호출해야 한다.

        Args:
            sim_id: 업데이트할 Simulation ID
            output_json: 시뮬레이션 결과 (JSON-serializable dict)

        Returns:
            업데이트된 Simulation, 또는 None (sim_id 미존재 시)
        """
        sim = await self.get_by_id(sim_id)
        if not sim:
            return None
        sim.status = "COMPLETED"
        sim.output_json = output_json
        sim.finished_at = datetime.utcnow()
        await self.session.flush()
        await self.session.refresh(sim)
        return sim

    async def update_failed(
        self, sim_id: int, error_message: str
    ) -> Optional[Simulation]:
        """
        Simulation을 FAILED로 전환하고 에러 메시지를 기록한다. [TX3 전용]

        Job 상태 업데이트는 이 메서드의 책임이 아니다.
        호출부(task)에서 같은 TX 안에 JobRepo.update_status()를 함께 호출해야 한다.

        Args:
            sim_id: 업데이트할 Simulation ID
            error_message: 에러 설명 ([ERROR_CODE] message | Context: ... 형식 권장)

        Returns:
            업데이트된 Simulation, 또는 None (sim_id 미존재 시)
        """
        sim = await self.get_by_id(sim_id)
        if not sim:
            return None
        sim.status = "FAILED"
        sim.error_message = error_message
        sim.finished_at = datetime.utcnow()
        await self.session.flush()
        await self.session.refresh(sim)
        return sim
