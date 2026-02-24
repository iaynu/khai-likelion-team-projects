"""
YReport Repository — minimum CRUD tests.

Uses in-memory SQLite via conftest.py (setup_db + db_session fixtures).
A COMPLETED Simulation is created as a prerequisite for each test
(simulation_id FK is not enforced in SQLite, but we keep it realistic).
"""
import pytest
from app.repositories.simulation_repo import SimulationRepo
from app.repositories.y_report_repo import YReportRepo

_SIM_OUTPUT = {
    "target_store": "test_store",
    "before": {"output_dir": "/b"},
    "after": {"output_dir": "/a"},
}

_YR_OUTPUT = {
    "structured": {"visit_change_pct": 20.0},
    "service_meta": {"engine": "YReportAdapter"},
}


@pytest.mark.asyncio
async def test_create_pending(db_session):
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_completed(
            store_source_id="test_store",
            x_report_id=None,
            selected_strategy_ids=[],
            days=1,
            seed=42,
            output_json=_SIM_OUTPUT,
        )
        yr = await YReportRepo(db_session).create_pending(
            store_source_id="test_store",
            simulation_id=sim.id,
        )
    assert yr.id is not None
    assert yr.status == "PENDING"
    assert yr.simulation_id == sim.id
    assert yr.store_source_id == "test_store"


@pytest.mark.asyncio
async def test_get_by_id(db_session):
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_completed(
            store_source_id="test_store",
            x_report_id=None,
            selected_strategy_ids=[],
            days=1,
            seed=42,
            output_json=_SIM_OUTPUT,
        )
        yr = await YReportRepo(db_session).create_pending(
            store_source_id="test_store",
            simulation_id=sim.id,
        )
        yr_id = yr.id

    result = await YReportRepo(db_session).get_by_id(yr_id)
    assert result is not None
    assert result.id == yr_id
    assert result.status == "PENDING"


@pytest.mark.asyncio
async def test_get_by_id_missing(db_session):
    result = await YReportRepo(db_session).get_by_id(99999)
    assert result is None


@pytest.mark.asyncio
async def test_create_completed(db_session):
    markdown = "# 비교 분석 보고서"
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_completed(
            store_source_id="test_store",
            x_report_id=None,
            selected_strategy_ids=[],
            days=1,
            seed=42,
            output_json=_SIM_OUTPUT,
        )
        yr = await YReportRepo(db_session).create_completed(
            store_source_id="test_store",
            simulation_id=sim.id,
            output_json=_YR_OUTPUT,
            markdown=markdown,
        )
    assert yr.status == "COMPLETED"
    assert yr.output_json == _YR_OUTPUT
    assert yr.markdown == markdown
    assert yr.finished_at is not None
