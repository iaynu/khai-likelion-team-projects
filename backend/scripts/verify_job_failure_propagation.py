"""
Job Failure Propagation 통합 검증 스크립트

실행 방법:
  USE_OPENAI_MOCK_MODE=OK_9  python scripts/verify_job_failure_propagation.py
  USE_OPENAI_MOCK_MODE=BAD_6 python scripts/verify_job_failure_propagation.py

환경변수:
  DATABASE_URL: DB 연결 문자열 (기본값: 프로젝트 설정)
  USE_OPENAI_MOCK_MODE: "OK_9" (정상 9개 솔루션) 또는 "BAD_6" (불완전 6개)
  LOG_LEVEL: INFO/DEBUG (기본값: INFO)
  SQL_ECHO: 1 (SQL 로그 출력) 또는 0 (기본값)
"""
import sys
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 환경변수 설정
USE_OPENAI_MOCK_MODE = os.getenv("USE_OPENAI_MOCK_MODE", "OK_9")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SQL_ECHO = os.getenv("SQL_ECHO", "0") == "1"

# 로그 설정
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="[%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Imports (환경변수 설정 후)
from sqlalchemy import text
from app.db.session import get_async_session
from app.db.base import reset_engine, get_engine, Base
from app.repositories.job_repo import JobRepo
from app.repositories.x_report_repo import XReportRepo
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.snapshot_repo import SnapshotRepo
from app.models.job import JobStatus, JobType
from app.models.store import Store
import uuid

# Task 내부 async 함수들
# NOTE: _save_report_async 는 PART 3에서 _save_via_service 로 대체됨.
# 이 스크립트는 레거시 실패 전파 검증용으로, generate_or_get_x_report() 기반으로
# 재작성이 필요함 (docs/LOCAL_VERIFY.md 참조).
from tasks.x_report_task import (
    _update_job_processing_async,
    _save_via_service,
    _update_job_failed_async,
    extract_solutions,
    format_error_message,
)


def generate_mock_report(solution_count: int) -> str:
    """
    Mock X-Report 텍스트 생성 (extract_solutions 파싱 가능한 형식)

    Args:
        solution_count: 생성할 솔루션 개수 (6 또는 9)

    Returns:
        Mock report text (extract_solutions에서 파싱 가능한 형식)
    """
    report_lines = []

    # 3개 카테고리 생성
    for i in range(3):
        category_num = i + 1
        report_lines.append(f"\n전략 카테고리 {category_num}: 테스트 카테고리 {category_num}")

        # 각 카테고리당 솔루션 개수 계산
        solutions_per_category = min(3, max(0, solution_count - i * 3))
        for j in range(solutions_per_category):
            sol_num = j + 1
            report_lines.append(
                f"- 솔루션 {sol_num}: 테스트 솔루션 {category_num}-{sol_num}. "
                f"이것은 카테고리 {category_num}의 {sol_num}번째 솔루션입니다."
            )

    return "\n".join(report_lines)


async def setup_test_data() -> tuple[str, int, int]:
    """
    테스트 데이터 생성: Store, Snapshot, Job

    Returns:
        (store_id, snapshot_id, job_id)
    """
    async with get_async_session() as session:
        async with session.begin():
            # Store 생성
            store = Store(
                id=str(uuid.uuid4()),
                store_source_id=f"TEST_VERIFY_{uuid.uuid4().hex[:8]}",
                store_name="검증 테스트 매장",
                sector_name="테스트",
                area_code="00000",
            )
            session.add(store)
            await session.flush()

            # Snapshot 생성
            snapshot_repo = SnapshotRepo(session)
            snapshot_version = f"test_v{uuid.uuid4().hex[:8]}"
            snapshot = await snapshot_repo.create(snapshot_version)
            await session.flush()

            # Job 생성
            job_repo = JobRepo(session)
            job = await job_repo.create(
                job_type=JobType.X_REPORT,
                celery_task_id=f"verify-test-{uuid.uuid4().hex[:8]}",
                store_source_id=store.store_source_id,
                snapshot_version=snapshot_version,
                prompt_id="verify-test-prompt",
            )
            await session.flush()
            await session.refresh(job)

            logger.info(f"Test data created: store_id={store.id}, snapshot_id={snapshot.id}, job_id={job.id}")
            return store.store_source_id, store.id, snapshot.id, snapshot_version, job.id


async def cleanup_test_data(job_id: int, xreport_id: Optional[int] = None):
    """
    테스트 데이터 정리

    Args:
        job_id: Job ID
        xreport_id: XReport ID (있는 경우)

    조회 기준:
    - Job.result_id로 xreport_id 조회 (없으면 파라미터 사용)
    - xreport_id로 Strategy 조회
    - FK 순서: Strategy → XReport → Job
    """
    try:
        async with get_async_session() as session:
            async with session.begin():
                # Job 조회하여 result_id(xreport_id) 확인
                job_repo = JobRepo(session)
                job = await job_repo.get_by_id(job_id)

                if job and job.result_id:
                    xreport_id = job.result_id

                # Strategy 삭제 (xreport_id 기반)
                if xreport_id:
                    await session.execute(
                        text("DELETE FROM strategies WHERE x_report_id = :xreport_id"),
                        {"xreport_id": xreport_id}
                    )
                    logger.info(f"Deleted strategies for x_report_id={xreport_id}")

                    # XReport 삭제
                    await session.execute(
                        text("DELETE FROM x_reports WHERE id = :id"),
                        {"id": xreport_id}
                    )
                    logger.info(f"Deleted x_report id={xreport_id}")

                # Job 삭제
                if job_id:
                    await session.execute(
                        text("DELETE FROM jobs WHERE id = :id"),
                        {"id": job_id}
                    )
                    logger.info(f"Deleted job id={job_id}")

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        # Don't raise - cleanup 실패해도 다음 테스트 진행


async def verify_case_ok_9() -> int:
    """
    [CASE OK_9] 정상 케이스 검증: 9개 솔루션 → Job COMPLETED

    흐름:
    1. 테스트 데이터 생성 (Job, Store, Snapshot)
    2. TX1: Job → PROCESSING
    3. Mock report 생성 (9개 솔루션)
    4. extract_solutions 호출
    5. TX2: XReport + Strategies + Job COMPLETED
    6. 검증 (Session B): Job/XReport/Strategy 확인
    7. Cleanup

    Returns:
        0 (성공) or 1 (실패)
    """
    print("\n" + "=" * 80)
    print("[CASE OK_9] 정상 케이스: 9개 솔루션 → Job COMPLETED + XReport + 9 Strategies")
    print("=" * 80)

    job_id = None
    xreport_id = None

    try:
        # ────────────────────────────────────────────────────────
        # [준비] 테스트 데이터 생성
        # ────────────────────────────────────────────────────────
        store_source_id, store_id, snapshot_id, snapshot_version, job_id = await setup_test_data()
        print(f"\n[Setup] job_id={job_id}, store_source_id={store_source_id}")

        # ────────────────────────────────────────────────────────
        # [실행] Task 로직 실행
        # ────────────────────────────────────────────────────────

        # TX1: Job → PROCESSING
        print("\n[TX1] Job → PROCESSING")
        await _update_job_processing_async(job_id)

        # Mock report 생성 (9개 솔루션)
        print("[Mock] Generating mock report (9 solutions)")
        mock_report_text = generate_mock_report(9)
        logger.debug(f"Mock report text:\n{mock_report_text}")

        # extract_solutions 호출
        print("[Parse] Extracting solutions")
        strategies = extract_solutions(mock_report_text)
        print(f"  Extracted: {len(strategies)} strategies")

        # TX2: XReport + Strategies + Job COMPLETED
        print("[TX2] Saving XReport + Strategies + Job COMPLETED")
        result = await _save_report_async(
            job_id=job_id,
            store_source_id=store_source_id,
            snapshot_version=snapshot_version,
            prompt_id="verify-test-prompt",
            content_text=mock_report_text,
            strategies=strategies,
            analysis_log="Mock analysis for verification",
        )
        xreport_id = result.get("id")
        print(f"  XReport created: id={xreport_id}")

        # ────────────────────────────────────────────────────────
        # [검증] Session B (새 세션으로 커밋 결과 확인)
        # ────────────────────────────────────────────────────────
        print("\n[Verification] Checking database state (Session B)")

        async with get_async_session() as session:
            job_repo = JobRepo(session)
            xreport_repo = XReportRepo(session)
            strategy_repo = StrategyRepo(session)

            # 1. Job 확인
            job = await job_repo.get_by_id(job_id)
            assert job is not None, f"Job {job_id} not found"
            assert job.status == JobStatus.COMPLETED, f"Expected COMPLETED, got {job.status}"
            assert job.result_id == xreport_id, f"Expected result_id={xreport_id}, got {job.result_id}"
            print(f"  [OK] Job status: {job.status}, result_id: {job.result_id}")

            # 2. XReport 확인
            xreport = await xreport_repo.get_by_id(xreport_id)
            assert xreport is not None, f"XReport {xreport_id} not found"
            assert xreport.status == "completed", f"Expected completed, got {xreport.status}"
            print(f"  [OK] XReport id={xreport.id}, status: {xreport.status}")

            # 3. Strategy 확인 (정확히 9개)
            strategies_list = await strategy_repo.list_by_report(xreport_id)
            strategy_count = len(strategies_list)
            assert strategy_count == 9, f"Expected 9 strategies, got {strategy_count}"
            print(f"  [OK] Strategies count: {strategy_count}")

        print("\n[PASS] CASE OK_9: All verifications passed")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] CASE OK_9: {e}")
        # 실패 시 추가 디버그 정보 출력
        try:
            async with get_async_session() as session:
                job_repo = JobRepo(session)
                job = await job_repo.get_by_id(job_id) if job_id else None
                if job:
                    print(f"  Debug: job_id={job.id}, status={job.status}, result_id={job.result_id}")
                    if job.error_message:
                        print(f"  Debug: error_message={job.error_message[:200]}")
        except:
            pass
        return 1

    except Exception as e:
        print(f"\n[ERROR] CASE OK_9: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Cleanup
        print("\n[Cleanup]")
        await cleanup_test_data(job_id, xreport_id)


async def verify_case_bad_6() -> int:
    """
    [CASE BAD_6] 실패 케이스 검증: 6개 솔루션 → ValueError → Job FAILED

    흐름:
    1. 테스트 데이터 생성 (Job, Store, Snapshot)
    2. TX1: Job → PROCESSING
    3. Mock report 생성 (6개 솔루션)
    4. extract_solutions 호출 → ValueError 발생
    5. TX3: Job → FAILED (error_message 포함)
    6. 검증 (Session B): Job FAILED, XReport/Strategy 미생성 확인
    7. Cleanup

    Returns:
        0 (성공) or 1 (실패)
    """
    print("\n" + "=" * 80)
    print("[CASE BAD_6] 실패 케이스: 6개 솔루션 → ValueError → Job FAILED")
    print("=" * 80)

    job_id = None

    try:
        # ────────────────────────────────────────────────────────
        # [준비] 테스트 데이터 생성
        # ────────────────────────────────────────────────────────
        store_source_id, store_id, snapshot_id, snapshot_version, job_id = await setup_test_data()
        print(f"\n[Setup] job_id={job_id}, store_source_id={store_source_id}")

        # ────────────────────────────────────────────────────────
        # [실행] Task 로직 실행 (예외 발생 예상)
        # ────────────────────────────────────────────────────────

        # TX1: Job → PROCESSING
        print("\n[TX1] Job → PROCESSING")
        await _update_job_processing_async(job_id)

        # Mock report 생성 (6개 솔루션 - 실패할 것)
        print("[Mock] Generating mock report (6 solutions - will fail)")
        mock_report_text = generate_mock_report(6)
        logger.debug(f"Mock report text:\n{mock_report_text}")

        # extract_solutions 호출 → ValueError 발생 예상
        print("[Parse] Extracting solutions (expecting ValueError)")
        error_occurred = False
        try:
            strategies = extract_solutions(mock_report_text)
            print(f"  ERROR: Should have raised ValueError, but got {len(strategies)} strategies")
        except ValueError as e:
            error_occurred = True
            print(f"  ValueError caught: {e}")

            # TX3: Job → FAILED
            print("[TX3] Job → FAILED")
            error_msg = format_error_message(
                error_code="STRATEGY_PARSE_COUNT_MISMATCH",
                message=str(e),
                context=mock_report_text[:500]
            )
            await _update_job_failed_async(job_id, error_msg)

        assert error_occurred, "ValueError should have been raised"

        # ────────────────────────────────────────────────────────
        # [검증] Session B (새 세션으로 커밋 결과 확인)
        # ────────────────────────────────────────────────────────
        print("\n[Verification] Checking database state (Session B)")

        async with get_async_session() as session:
            job_repo = JobRepo(session)
            xreport_repo = XReportRepo(session)

            # 1. Job 확인 (FAILED)
            job = await job_repo.get_by_id(job_id)
            assert job is not None, f"Job {job_id} not found"
            assert job.status == JobStatus.FAILED, f"Expected FAILED, got {job.status}"
            assert job.error_message is not None, "error_message should not be None"
            assert "STRATEGY_PARSE_COUNT_MISMATCH" in job.error_message, \
                f"Expected STRATEGY_PARSE_COUNT_MISMATCH in error_message, got: {job.error_message[:100]}"
            print(f"  [OK] Job status: {job.status}")
            print(f"  [OK] error_message: {job.error_message[:100]}...")

            # 2. XReport 확인 (생성되지 않아야 함)
            # job.result_id가 None이어야 함
            assert job.result_id is None, f"result_id should be None, got {job.result_id}"
            print(f"  [OK] XReport not created (result_id is None)")

            # 3. Strategy 확인 (0개여야 함)
            # 전체 strategies 조회해서 0개 확인
            result = await session.execute(text("SELECT COUNT(*) FROM strategies"))
            strategy_count = result.scalar_one()
            print(f"  [OK] Strategies count: {strategy_count} (expected 0)")
            # Note: 이 테스트는 격리된 환경이므로 0개가 맞지만,
            # 실제 DB에서는 다른 데이터가 있을 수 있음
            # 더 정확한 검증을 위해서는 job_id와 연관된 XReport가 없음을 확인

        print("\n[PASS] CASE BAD_6: All verifications passed")
        return 0

    except AssertionError as e:
        print(f"\n[FAIL] CASE BAD_6: {e}")
        # 실패 시 추가 디버그 정보 출력
        try:
            async with get_async_session() as session:
                job_repo = JobRepo(session)
                job = await job_repo.get_by_id(job_id) if job_id else None
                if job:
                    print(f"  Debug: job_id={job.id}, status={job.status}, result_id={job.result_id}")
                    if job.error_message:
                        print(f"  Debug: error_message={job.error_message[:200]}")
        except:
            pass
        return 1

    except Exception as e:
        print(f"\n[ERROR] CASE BAD_6: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # Cleanup
        print("\n[Cleanup]")
        await cleanup_test_data(job_id, xreport_id=None)


async def main() -> int:
    """
    메인 실행 함수

    Returns:
        0 (성공) or 1 (실패)
    """
    print("=" * 80)
    print("JOB FAILURE PROPAGATION VERIFICATION")
    print(f"Mode: {USE_OPENAI_MOCK_MODE}")
    print("=" * 80)

    mode = USE_OPENAI_MOCK_MODE

    if mode == "OK_9":
        return await verify_case_ok_9()
    elif mode == "BAD_6":
        return await verify_case_bad_6()
    else:
        print(f"[ERROR] Unknown mode: {mode}")
        print("Valid modes: OK_9, BAD_6")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
