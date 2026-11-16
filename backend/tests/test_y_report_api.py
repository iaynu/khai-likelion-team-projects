"""
Y-Report API — minimum smoke tests.

POST /y-reports → 201, job_id + y_report_id returned (COMPLETED sim 필요).
404 for missing sim, 422 for non-COMPLETED sim.
GET  /y-reports/{id} → 404 for missing.

YReportService/Adapter(실제 분석 엔진)는 호출되지 않음.
Celery .delay()를 mock하여 DB 저장 흐름과 task 호출 인자 계약을 검증한다.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.db.session import get_async_session
from app.repositories.simulation_repo import SimulationRepo

_SIM_OUTPUT = {
    "target_store": "test_store",
    "before": {"output_dir": "/mock/before"},
    "after": {"output_dir": "/mock/after"},
}


async def _make_completed_sim() -> int:
    """테스트 DB에 COMPLETED Simulation을 생성하고 id를 반환한다."""
    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_completed(
                store_source_id="test_store",
                x_report_id=None,
                selected_strategy_ids=[],
                days=1,
                seed=42,
                output_json=_SIM_OUTPUT,
            )
    return sim.id


async def _make_pending_sim() -> int:
    """테스트 DB에 PENDING Simulation을 생성하고 id를 반환한다."""
    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id="pending_store",
                x_report_id=None,
                selected_strategy_ids=[],
                days=1,
                seed=42,
            )
    return sim.id


@pytest.mark.asyncio
async def test_post_y_report_returns_job_id(client):
    sim_id = await _make_completed_sim()

    with patch("tasks.y_report_task.generate_y_report.delay") as mock_delay:
        mock_delay.return_value = MagicMock(id="mock-celery-id")
        resp = await client.post("/y-reports", json={"simulation_id": sim_id})

    assert resp.status_code == 201
    data = resp.json()
    assert "job_id" in data
    assert "y_report_id" in data
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert isinstance(data["y_report_id"], int)

    # .delay() 호출 인자 계약 검증
    mock_delay.assert_called_once_with(
        job_id=data["job_id"],
        y_report_id=data["y_report_id"],
        simulation_id=sim_id,
        x_report_id=None,
        options={},
    )


@pytest.mark.asyncio
async def test_post_y_report_simulation_not_found(client):
    resp = await client.post("/y-reports", json={"simulation_id": 99999})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_y_report_simulation_not_completed(client):
    """PENDING 상태의 Simulation → 422."""
    sim_id = await _make_pending_sim()
    resp = await client.post("/y-reports", json={"simulation_id": sim_id})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_y_report_not_found(client):
    resp = await client.get("/y-reports/99999")
    assert resp.status_code == 404
