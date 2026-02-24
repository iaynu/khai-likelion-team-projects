"""
Simulation API — minimum smoke tests.

POST /simulations → 201, job_id + sim_id returned.
GET  /simulations/{id} → 200 PENDING / 404.

SimulationService (실제 LLM/엔진)은 호출되지 않음.
Celery .delay()를 mock하여 DB 저장 흐름과 task 호출 인자 계약을 검증한다.
"""
import pytest
from unittest.mock import MagicMock, patch

_SIM_BODY = {
    "store_source_id": "test_store",
    "selected_strategy_ids": ["S1_A"],
    "days": 1,
    "seed": 42,
    "agent_count": 5,
    "max_concurrent_llm_calls": 5,
}


@pytest.mark.asyncio
async def test_post_simulation_returns_job_id(client):
    with patch("tasks.simulation_task.run_simulation.delay") as mock_delay:
        mock_delay.return_value = MagicMock(id="mock-celery-id")
        resp = await client.post("/simulations", json=_SIM_BODY)

    assert resp.status_code == 201
    data = resp.json()
    assert "job_id" in data
    assert "sim_id" in data
    assert data["status"] == "pending"
    assert isinstance(data["job_id"], int)
    assert isinstance(data["sim_id"], int)

    # .delay() 호출 인자 계약 검증
    mock_delay.assert_called_once_with(
        job_id=data["job_id"],
        sim_id=data["sim_id"],
        store_source_id="test_store",
        selected_strategy_ids=["S1_A"],
        days=1,
        seed=42,
        agent_count=5,
        max_concurrent_llm_calls=5,
    )


@pytest.mark.asyncio
async def test_get_simulation_returns_pending(client):
    with patch("tasks.simulation_task.run_simulation.delay") as mock_delay:
        mock_delay.return_value = MagicMock(id="mock-celery-id")
        post_resp = await client.post("/simulations", json=_SIM_BODY)

    assert post_resp.status_code == 201
    sim_id = post_resp.json()["sim_id"]

    get_resp = await client.get(f"/simulations/{sim_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == sim_id
    assert data["status"] == "PENDING"
    assert data["store_source_id"] == "test_store"


@pytest.mark.asyncio
async def test_get_simulation_not_found(client):
    resp = await client.get("/simulations/99999")
    assert resp.status_code == 404
