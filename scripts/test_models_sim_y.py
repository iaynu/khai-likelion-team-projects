"""
Simulation + YReport CRUD 스모크 테스트

uvicorn/Celery 없이 로컬에서 바로 실행:
    python scripts/test_models_sim_y.py

완료 조건: 전체 PASS 후 MODELS_OK 출력
"""
import asyncio
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


def header(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────
# Simulation CRUD
# ──────────────────────────────────────────────────────────────

async def test_simulation_crud() -> bool:
    header("Simulation CRUD")
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo

    ok = True

    # 1. create
    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id="__test_store__",
                x_report_id=None,
                selected_strategy_ids=["S1_A", "S2_B"],
                days=3,
                seed=42,
                input_json={"test": True, "korean": "한글"},
                settings_json={"agent_count": 10},
            )
            sim_id = sim.id

    print(f"  [OK] create: sim_id={sim_id}, status={sim.status}")
    assert sim.status == "PENDING", f"status 불일치: {sim.status}"

    # 2. get_by_id
    async with get_async_session() as session:
        fetched = await SimulationRepo(session).get_by_id(sim_id)
    assert fetched is not None, "get_by_id returned None"
    assert fetched.store_source_id == "__test_store__"
    assert fetched.selected_strategy_ids == ["S1_A", "S2_B"]
    assert fetched.seed == 42
    print(f"  [OK] get_by_id: store={fetched.store_source_id}")

    # 3. update_completed
    output = {
        "target_store": "__test_store__",
        "before": {"output_dir": "/tmp/before", "visit_count": 10},
        "after": {"output_dir": "/tmp/after", "visit_count": 15},
        "korean_key": "한글값",
    }
    async with get_async_session() as session:
        async with session.begin():
            updated = await SimulationRepo(session).update_completed(sim_id, output)
    assert updated.status == "COMPLETED"
    assert updated.output_json["target_store"] == "__test_store__"
    assert updated.output_json["korean_key"] == "한글값"
    # JSON 직렬화 확인
    serialized = json.dumps(updated.output_json, ensure_ascii=False)
    assert "한글값" in serialized
    print(f"  [OK] update_completed: status={updated.status}, json={len(serialized)}chars")

    # 4. update_failed (별도 sim)
    async with get_async_session() as session:
        async with session.begin():
            sim2 = await SimulationRepo(session).create_pending(
                store_source_id="__test_store__",
                x_report_id=None,
                selected_strategy_ids=[],
                days=1,
                seed=0,
            )
            sim2_id = sim2.id

    async with get_async_session() as session:
        async with session.begin():
            failed = await SimulationRepo(session).update_failed(sim2_id, "mock error msg")
    assert failed.status == "FAILED"
    assert "mock error msg" in failed.error_message
    print(f"  [OK] update_failed: status={failed.status}, error={failed.error_message[:30]}")

    # 5. list_by_store
    async with get_async_session() as session:
        sims = await SimulationRepo(session).list_by_store("__test_store__", limit=10)
    assert len(sims) >= 2, f"list_by_store 결과 {len(sims)}개 (2 이상 예상)"
    print(f"  [OK] list_by_store: {len(sims)}개 반환")

    print("  [PASS] Simulation CRUD 전체 통과\n")
    return ok


# ──────────────────────────────────────────────────────────────
# YReport CRUD
# ──────────────────────────────────────────────────────────────

async def test_y_report_crud() -> bool:
    header("YReport CRUD")
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.y_report_repo import YReportRepo

    ok = True

    # 사전 조건: Simulation COMPLETED 레코드 필요
    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).create_pending(
                store_source_id="__yr_test_store__",
                x_report_id=None,
                selected_strategy_ids=["S1_A"],
                days=2,
                seed=7,
            )
            sim_id = sim.id

    async with get_async_session() as session:
        async with session.begin():
            await SimulationRepo(session).update_completed(
                sim_id, {"target_store": "__yr_test_store__"}
            )

    # 1. create
    async with get_async_session() as session:
        async with session.begin():
            yr = await YReportRepo(session).create_pending(
                store_source_id="__yr_test_store__",
                simulation_id=sim_id,
                x_report_id=None,
                input_json={"request": "smoke_test", "korean": "한글입력"},
            )
            yr_id = yr.id

    print(f"  [OK] create: yr_id={yr_id}, status={yr.status}")
    assert yr.status == "PENDING"
    assert yr.simulation_id == sim_id

    # 2. get_by_id
    async with get_async_session() as session:
        fetched = await YReportRepo(session).get_by_id(yr_id)
    assert fetched is not None
    assert fetched.store_source_id == "__yr_test_store__"
    assert fetched.input_json["korean"] == "한글입력"
    print(f"  [OK] get_by_id: store={fetched.store_source_id}")

    # 3. update_completed
    output_metrics = {
        "visit_change_pct": 25.5,
        "revenue_diff": 120000,
        "top_strategy": "S1_A",
        "korean_note": "방문율 증가",
    }
    md_text = "# Y-Report\n## 방문 변화\n- 방문율 25.5% 증가"
    async with get_async_session() as session:
        async with session.begin():
            updated = await YReportRepo(session).update_completed(yr_id, output_metrics, md_text)
    assert updated.status == "COMPLETED"
    assert updated.output_json["visit_change_pct"] == 25.5
    assert updated.output_json["korean_note"] == "방문율 증가"
    assert "방문율" in updated.markdown
    serialized = json.dumps(updated.output_json, ensure_ascii=False)
    assert "방문율" in serialized
    print(f"  [OK] update_completed: status={updated.status}, markdown={len(updated.markdown)}chars")

    # 4. update_failed (별도 yr)
    async with get_async_session() as session:
        async with session.begin():
            yr2 = await YReportRepo(session).create_pending(
                store_source_id="__yr_test_store__",
                simulation_id=sim_id,
            )
            yr2_id = yr2.id

    async with get_async_session() as session:
        async with session.begin():
            failed = await YReportRepo(session).update_failed(yr2_id, "yr mock error")
    assert failed.status == "FAILED"
    assert "yr mock error" in failed.error_message
    print(f"  [OK] update_failed: status={failed.status}")

    # 5. list_by_simulation
    async with get_async_session() as session:
        reports = await YReportRepo(session).list_by_simulation(sim_id)
    assert len(reports) >= 2, f"list_by_simulation 결과 {len(reports)}개 (2 이상 예상)"
    print(f"  [OK] list_by_simulation: {len(reports)}개 반환")

    print("  [PASS] YReport CRUD 전체 통과\n")
    return ok


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main():
    header("Simulation + YReport CRUD 스모크 테스트")
    print("  uvicorn/Celery 없이 DB 직접 테스트")

    results = []
    try:
        results.append(await test_simulation_crud())
    except Exception as e:
        print(f"  [FAIL] Simulation CRUD 예외: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_y_report_crud())
    except Exception as e:
        print(f"  [FAIL] YReport CRUD 예외: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    header("최종 결과")
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} PASS")

    if passed == total:
        print("  MODELS_OK")
        sys.exit(0)
    else:
        print("  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
