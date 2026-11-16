"""
Simulation Repository — minimum CRUD tests.

Uses in-memory SQLite via conftest.py (setup_db + db_session fixtures).
"""
import pytest
from app.repositories.simulation_repo import SimulationRepo

_OUTPUT_JSON = {
    "target_store": "test_store",
    "before": {"output_dir": "/b"},
    "after": {"output_dir": "/a"},
    "meta": {"engine": "run_full_pipeline", "timing_ms": 500},
}


@pytest.mark.asyncio
async def test_create_pending(db_session):
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_pending(
            store_source_id="test_store",
            x_report_id=None,
            selected_strategy_ids=["S1_A"],
            days=1,
            seed=42,
        )
    assert sim.id is not None
    assert sim.status == "PENDING"
    assert sim.store_source_id == "test_store"
    assert sim.selected_strategy_ids == ["S1_A"]


@pytest.mark.asyncio
async def test_get_by_id(db_session):
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_pending(
            store_source_id="get_store",
            x_report_id=None,
            selected_strategy_ids=[],
            days=1,
            seed=0,
        )
        sim_id = sim.id

    result = await SimulationRepo(db_session).get_by_id(sim_id)
    assert result is not None
    assert result.id == sim_id
    assert result.status == "PENDING"


@pytest.mark.asyncio
async def test_get_by_id_missing(db_session):
    result = await SimulationRepo(db_session).get_by_id(99999)
    assert result is None


@pytest.mark.asyncio
async def test_create_completed(db_session):
    async with db_session.begin():
        sim = await SimulationRepo(db_session).create_completed(
            store_source_id="comp_store",
            x_report_id=None,
            selected_strategy_ids=[],
            days=1,
            seed=42,
            output_json=_OUTPUT_JSON,
        )
    assert sim.status == "COMPLETED"
    assert sim.output_json == _OUTPUT_JSON
    assert sim.finished_at is not None
