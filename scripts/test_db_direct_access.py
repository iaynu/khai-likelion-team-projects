"""
DB 직접 접근 유틸 검증 스크립트

목적:
1. get_async_session() context manager 동작 확인
2. bulk_create(9개 strategies) flush 검증
3. create_completed() XReport 생성 검증
"""
import sys
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# SQLite in-memory 테스트를 위한 설정
import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.db.session import get_async_session
from app.db.base import reset_engine, get_engine, Base
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.x_report_repo import XReportRepo


async def setup_database():
    """테스트용 in-memory DB 스키마 생성"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[Setup] Database schema created")


async def test_get_async_session():
    """get_async_session() context manager 테스트"""
    print("\n" + "=" * 80)
    print("Test 1: get_async_session() context manager")
    print("=" * 80)

    async with get_async_session() as session:
        print(f"Session created: {session}")
        print(f"Session type: {type(session).__name__}")
        assert session is not None
        print("PASS: Session context manager works")


async def test_bulk_create():
    """bulk_create() 9개 strategies flush 검증"""
    print("\n" + "=" * 80)
    print("Test 2: StrategyRepo.bulk_create() with 9 strategies")
    print("=" * 80)

    # 1. XReport 생성 (strategies를 연결하기 위해 필요)
    async with get_async_session() as session:
        async with session.begin():
            xreport_repo = XReportRepo(session)
            xreport = await xreport_repo.create_completed(
                store_id="test_store_001",
                snapshot_id=1,
                prompt_id="test_prompt_v1",
                content_text="GPT generated report text here...",
                config_json={"version": "1.0"},
                model_meta={"model": "gpt-5.2", "tokens": 5000},
            )
            print(f"XReport created: id={xreport.id}")

    # 2. Strategies 9개 생성
    strategies_data = [
        {
            "solution_group": i // 3 + 1,  # 1, 1, 1, 2, 2, 2, 3, 3, 3
            "action_idx": i % 3,
            "title": f"Strategy {i+1}",
            "description": f"Description for strategy {i+1}",
            "config": {"priority": i + 1},
            "impact_mapping": {"revenue": i * 10},
        }
        for i in range(9)
    ]

    async with get_async_session() as session:
        async with session.begin():
            strategy_repo = StrategyRepo(session)
            created = await strategy_repo.bulk_create(
                x_report_id=xreport.id,
                strategies=strategies_data,
            )
            print(f"Strategies created: count={len(created)}")
            assert len(created) == 9, f"Expected 9 strategies, got {len(created)}"

            # Flush 후 ID 확인
            for idx, strat in enumerate(created):
                print(f"  [{idx+1}] id={strat.id}, group={strat.solution_group}, title={strat.title}")
                assert strat.id is not None, f"Strategy {idx+1} has no ID after flush"

    print("PASS: bulk_create() successfully created and flushed 9 strategies")


async def test_create_completed():
    """create_completed() XReport 생성 검증"""
    print("\n" + "=" * 80)
    print("Test 3: XReportRepo.create_completed()")
    print("=" * 80)

    async with get_async_session() as session:
        async with session.begin():
            xreport_repo = XReportRepo(session)
            report = await xreport_repo.create_completed(
                store_id="test_store_002",
                snapshot_id=2,
                prompt_id="test_prompt_v2",
                content_text="Full GPT report content here...",
                config_json={"version": "2.0", "model": "gpt-5.2"},
                model_meta={"tokens": 8000, "cost": 0.05},
            )

            print(f"XReport created:")
            print(f"  id={report.id}")
            print(f"  status={report.status}")
            print(f"  store_id={report.store_id}")
            print(f"  content_text length={len(report.content_text or '')}")
            print(f"  config_json={report.config_json}")

            assert report.id is not None
            assert report.status == "completed"
            assert report.content_text is not None
            assert len(report.content_text) > 0

    print("PASS: create_completed() successfully created XReport")


async def test_transaction_atomicity():
    """트랜잭션 원자성 검증: 중간 실패 시 rollback"""
    print("\n" + "=" * 80)
    print("Test 4: Transaction atomicity (rollback on failure)")
    print("=" * 80)

    try:
        async with get_async_session() as session:
            async with session.begin():
                xreport_repo = XReportRepo(session)
                strategy_repo = StrategyRepo(session)

                # XReport 생성
                report = await xreport_repo.create_completed(
                    store_id="test_store_rollback",
                    snapshot_id=99,
                    prompt_id="test_rollback",
                    content_text="This should be rolled back",
                )
                print(f"XReport created (will rollback): id={report.id}")

                # Strategies 생성 (3개만)
                strategies = [
                    {
                        "solution_group": 1,
                        "action_idx": i,
                        "title": f"Strategy {i}",
                        "description": "Test",
                        "config": {},
                        "impact_mapping": {},
                    }
                    for i in range(3)
                ]
                created = await strategy_repo.bulk_create(report.id, strategies)
                print(f"Strategies created (will rollback): count={len(created)}")

                # 의도적으로 에러 발생
                raise ValueError("Intentional error to test rollback")

    except ValueError as e:
        print(f"Exception caught: {e}")
        print("Transaction should have been rolled back")

    # Rollback 검증: 해당 XReport가 존재하지 않아야 함
    async with get_async_session() as session:
        xreport_repo = XReportRepo(session)
        result = await xreport_repo.get_cached("test_store_rollback", 99, "test_rollback")
        assert result is None, "XReport should not exist after rollback"
        print("PASS: Transaction correctly rolled back")


async def main():
    """모든 테스트 실행"""
    try:
        # 환경 초기화
        reset_engine()
        await setup_database()

        # 테스트 실행
        await test_get_async_session()
        await test_bulk_create()
        await test_create_completed()
        await test_transaction_atomicity()

        print("\n" + "=" * 80)
        print("ALL TESTS PASSED")
        print("=" * 80)
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        import traceback
        traceback.print_exc()
        return 1

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
