from typing import List
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.strategy import Strategy


class StrategyRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_report(self, x_report_id: int) -> List[Strategy]:
        result = await self.session.execute(
            select(Strategy)
            .where(Strategy.x_report_id == x_report_id)
            .order_by(Strategy.solution_group, Strategy.action_idx)
        )
        return list(result.scalars().all())

    async def count_by_report(self, x_report_id: int) -> int:
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.count(Strategy.id)).where(Strategy.x_report_id == x_report_id)
        )
        return result.scalar_one()

    async def bulk_create(self, x_report_id: int, strategies: List[dict]) -> List[Strategy]:
        """
        Bulk insert strategies (typically 9 items) without deleting existing ones.

        Args:
            x_report_id: XReport ID to associate strategies with
            strategies: List of dicts with keys: solution_group, action_idx, title, description, config, impact_mapping

        Returns:
            List of created Strategy objects
        """
        objs = [
            Strategy(
                x_report_id=x_report_id,
                solution_group=s["solution_group"],
                action_idx=s["action_idx"],
                title=s.get("title"),
                description=s.get("description"),
                config=s.get("config"),
                impact_mapping=s.get("impact_mapping"),
            )
            for s in strategies
        ]
        self.session.add_all(objs)
        await self.session.flush()
        return objs

    async def replace_all(self, x_report_id: int, strategies: List[dict]) -> List[Strategy]:
        """Delete existing + bulk insert for atomic replacement."""
        await self.session.execute(
            delete(Strategy).where(Strategy.x_report_id == x_report_id)
        )
        objs = [
            Strategy(
                x_report_id=x_report_id,
                solution_group=s["solution_group"],
                action_idx=s["action_idx"],
                title=s.get("title"),
                description=s.get("description"),
                config=s.get("config"),
                impact_mapping=s.get("impact_mapping"),
            )
            for s in strategies
        ]
        self.session.add_all(objs)
        await self.session.flush()
        return objs
