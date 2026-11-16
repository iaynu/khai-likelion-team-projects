"""
프론트 연동 계약(contract) 검증 스크립트

서버/워커 없이 in-process FastAPI 호출 + task helper 직접 실행으로
프론트가 기대하는 API 계약을 확인한다.

# 실행 방법:
#   python scripts/smoke_front_contract.py
#   LOG_LEVEL=DEBUG python scripts/smoke_front_contract.py

출력 포맷:
  JOB FIELDS: PASS
  SIMULATION CONTRACT: PASS
  Y_REPORT CONTRACT: PASS

구조:
  JOB FIELDS:          POST /simulations → GET /jobs/{id} 필수 필드(status/result_id/error_message) 존재 확인
  SIMULATION CONTRACT: POST /simulations → (mock TX) → GET /jobs + GET /simulations/{id}
  Y_REPORT CONTRACT:   POST /y-reports   → (mock TX) → GET /jobs + GET /y-reports/{id}

실제 엔진 호출은 verify_simulation_pipeline.py / verify_y_report_pipeline.py 의
ENGINE_MODE=real 에서 담당한다.
"""
import asyncio
import io
import logging
import os
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# ── 환경 변수 ────────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING").upper()
SQL_ECHO  = os.environ.get("SQL_ECHO", "0") == "1"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.core.config import settings  # noqa: E402
settings.SQL_ECHO = SQL_ECHO
from app.db.base import reset_engine  # noqa: E402
reset_engine()


# ──────────────────────────────────────────────────────────────
# Mock 데이터
# ──────────────────────────────────────────────────────────────

# POST /simulations 요청 본문
_SIM_POST_BODY = {
    "store_source_id": "smoke_test",
    "selected_strategy_ids": [],
    "days": 1,
    "seed": 42,
    "agent_count": 5,
    "max_concurrent_llm_calls": 5,
}

# simulation.output_json 포맷 — simulation_task._tx2_completed 에 전달하는 형식
# {**result["output"], "meta": result["meta"]} 와 동일
_MOCK_SIM_OUTPUT = {
    "target_store": "smoke_test",
    "before": {"output_dir": "/mock/before", "visit_count": 100},
    "after":  {"output_dir": "/mock/after",  "visit_count": 120},
    "meta": {
        "engine": "run_full_pipeline",
        "run_id": "00000000-0000-0000-0000-000000000099",
        "timing_ms": 100,
        "agent_count": 5,
        "days": 1,
        "max_concurrent_llm_calls": 5,
        "llm_tokens": None,
    },
}

# y_report.output_json 포맷 — y_report_task._tx2_completed 에 전달하는 형식
# {structured, adapter_meta, service_meta}
_MOCK_YR_OUTPUT = {
    "structured": {
        "total_visits_before": 100,
        "total_visits_after": 120,
        "visit_change_pct": 20.0,
    },
    "adapter_meta": {
        "target_store": "smoke_test",
        "before_dir": "/mock/before",
        "after_dir": "/mock/after",
        "report_path": "/mock/after/y_report/Comparison_Report.md",
        "x_report_keys": [],
    },
    "service_meta": {
        "engine": "YReportAdapter",
        "run_id": "00000000-0000-0000-0000-000000000098",
        "timing_ms": 100,
        "simulation_id": 0,   # check 내부에서 fixture sim_id로 교체
        "x_report_id": None,
    },
}
_MOCK_YR_MARKDOWN = "# 비교 분석\n\n방문율 20% 증가"


# ──────────────────────────────────────────────────────────────
# 출력 헬퍼
# ──────────────────────────────────────────────────────────────

def _header(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────
# 절대경로/서버 URL 스캐너
# ──────────────────────────────────────────────────────────────

# 응답 JSON 내에 포함되면 서버 환경이 노출되는 문자열 패턴
_ABS_PATH_PATTERNS = (
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "127.0.0.1:",
    "file://",
    "/mnt/",
    "/home/",
    "C:\\",
    "C:/Users",
    "D:\\",
    "/root/",
)


def _scan_for_server_paths(data: object, path: str = "") -> list:
    """JSON 응답을 재귀 순회하며 서버 절대경로/URL 포함 여부를 탐색한다.

    Returns:
        [(field_path, matched_pattern, value_snippet), ...]
    """
    found = []
    if isinstance(data, dict):
        for k, v in data.items():
            found.extend(_scan_for_server_paths(v, f"{path}.{k}"))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found.extend(_scan_for_server_paths(v, f"{path}[{i}]"))
    elif isinstance(data, str):
        for pattern in _ABS_PATH_PATTERNS:
            if pattern.lower() in data.lower():
                found.append((path, pattern, data[:100]))
                break   # 같은 값에서 중복 보고 방지
    return found


def _warn_if_server_paths(label: str, response_json: object) -> None:
    """응답에 서버 경로가 발견되면 WARN을 출력한다 (테스트 실패로 간주하지 않음).

    실제 엔진 결과에는 서버 filesystem 경로가 output_json 내에 포함될 수 있다.
    프론트는 output_json.adapter_meta.* 및 simulation.output_json.before/after.output_dir
    필드를 직접 사용하지 않도록 FRONTEND_CONTRACT.md를 참고하라.
    """
    issues = _scan_for_server_paths(response_json)
    if issues:
        for field, pattern, snippet in issues:
            print(f"  [WARN] 서버경로 노출 감지: field={field!r} pattern={pattern!r} "
                  f"value={snippet!r}")
        print(f"  [WARN] {label}: 위 필드는 프론트에서 사용하지 말 것 "
              f"(FRONTEND_CONTRACT.md 참고)")
    else:
        print(f"  [OK] {label}: 서버 절대경로/URL 미노출")


# ──────────────────────────────────────────────────────────────
# 계약 검증 공통 어서션
# ──────────────────────────────────────────────────────────────

# 프론트 폴링에서 반드시 기대하는 Job 응답 필드
_JOB_REQUIRED_FIELDS = ("id", "job_type", "status", "result_id", "error_message")


def _assert_job_fields(job_data: dict) -> None:
    """GET /jobs/{id} 응답에 프론트 필수 필드가 모두 있는지 확인한다."""
    missing = [f for f in _JOB_REQUIRED_FIELDS if f not in job_data]
    assert not missing, (
        f"Job 응답 필수 필드 누락: {missing} | 실제 키: {list(job_data.keys())}"
    )
    assert isinstance(job_data["status"], str), "status 가 문자열이 아님"


# ──────────────────────────────────────────────────────────────
# DB 정리 헬퍼 (finally 전용)
# ──────────────────────────────────────────────────────────────

async def _del_sim_and_job(sim_id: int, job_id: int) -> None:
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).get_by_id(sim_id)
            if sim:
                await session.delete(sim)
            job = await JobRepo(session).get_by_id(job_id)
            if job:
                await session.delete(job)


async def _del_yr_and_job(yr_id: int, job_id: int) -> None:
    from app.db.session import get_async_session
    from app.repositories.y_report_repo import YReportRepo
    from app.repositories.job_repo import JobRepo

    async with get_async_session() as session:
        async with session.begin():
            yr = await YReportRepo(session).get_by_id(yr_id)
            if yr:
                await session.delete(yr)
            job = await JobRepo(session).get_by_id(job_id)
            if job:
                await session.delete(job)


async def _del_sim(sim_id: int) -> None:
    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo

    async with get_async_session() as session:
        async with session.begin():
            sim = await SimulationRepo(session).get_by_id(sim_id)
            if sim:
                await session.delete(sim)


# ──────────────────────────────────────────────────────────────
# Check 1: JOB FIELDS
# ──────────────────────────────────────────────────────────────

async def check_job_fields(client) -> bool:
    """POST /simulations → GET /jobs/{id} → 필수 필드 존재 + 타입 확인 (pending 상태).

    Celery는 mock. TX 전환 없이 pending 상태 그대로 확인한다.
    """
    _header("JOB FIELDS 확인")

    sim_id: Optional[int] = None
    job_id: Optional[int] = None

    try:
        with patch("tasks.simulation_task.run_simulation.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="smoke-jf-celery")
            r = await client.post("/simulations", json=_SIM_POST_BODY)

        assert r.status_code == 201, f"POST /simulations → {r.status_code}: {r.text[:200]}"
        d = r.json()
        sim_id = d["sim_id"]
        job_id = d["job_id"]

        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200, f"GET /jobs/{job_id} → {r.status_code}"
        job_data = r.json()

        _assert_job_fields(job_data)

        # 초기 pending 상태에서 null 필드 타입 확인
        assert job_data["status"] == "pending",         f"초기 status 불일치: {job_data['status']}"
        assert job_data["result_id"] is None,           "pending 상태에서 result_id는 null이어야 함"
        assert job_data["error_message"] is None,       "pending 상태에서 error_message는 null이어야 함"

        print(f"  [OK] GET /jobs/{job_id} → 필수 필드 모두 존재")
        print(f"       status={job_data['status']!r}, result_id={job_data['result_id']}, "
              f"error_message={job_data['error_message']}")
        return True

    finally:
        if sim_id is not None and job_id is not None:
            await _del_sim_and_job(sim_id, job_id)


# ──────────────────────────────────────────────────────────────
# Check 2: SIMULATION CONTRACT
# ──────────────────────────────────────────────────────────────

async def check_simulation_contract(client) -> bool:
    """
    POST /simulations
      → mock TX (pending → processing → completed)
      → GET /jobs/{id}      (status=completed, result_id=sim_id)
      → GET /simulations/{id} (status=COMPLETED, output_json 있음)
    """
    _header("SIMULATION CONTRACT 확인")

    from tasks.simulation_task import _tx1_processing, _tx2_completed

    sim_id: Optional[int] = None
    job_id: Optional[int] = None

    try:
        # ── POST ───────────────────────────────────────────────
        with patch("tasks.simulation_task.run_simulation.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="smoke-sim-celery")
            r = await client.post("/simulations", json=_SIM_POST_BODY)

        assert r.status_code == 201, f"POST /simulations 실패: {r.status_code} {r.text[:200]}"
        d = r.json()
        assert "job_id" in d and "sim_id" in d, f"응답 필드 누락: {d}"
        assert d["status"] == "pending"
        sim_id = d["sim_id"]
        job_id = d["job_id"]
        print(f"  [OK] POST /simulations → job_id={job_id}, sim_id={sim_id}, status=pending")

        # ── Mock TX: pending → processing → completed ──────────
        await _tx1_processing(job_id)
        await _tx2_completed(job_id, sim_id, _MOCK_SIM_OUTPUT)

        # ── GET /jobs/{id} ─────────────────────────────────────
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        job_data = r.json()
        _assert_job_fields(job_data)
        assert job_data["status"] == "completed",   f"job status 불일치: {job_data['status']}"
        assert job_data["result_id"] == sim_id,     f"result_id 불일치: {job_data['result_id']}"
        assert job_data["error_message"] is None
        print(f"  [OK] GET /jobs/{job_id} → status=completed, result_id={sim_id}")

        # ── GET /simulations/{id} ──────────────────────────────
        r = await client.get(f"/simulations/{sim_id}")
        assert r.status_code == 200, f"GET /simulations/{sim_id} 실패: {r.status_code}"
        sim_data = r.json()
        assert sim_data["id"] == sim_id
        assert sim_data["status"] == "COMPLETED",   f"sim status 불일치: {sim_data['status']}"
        assert sim_data["output_json"] is not None, "output_json 없음"
        print(f"  [OK] GET /simulations/{sim_id} → status=COMPLETED, output_json 있음")
        _warn_if_server_paths("GET /simulations/{id}", sim_data)

        return True

    finally:
        if sim_id is not None and job_id is not None:
            await _del_sim_and_job(sim_id, job_id)


# ──────────────────────────────────────────────────────────────
# Check 3: Y_REPORT CONTRACT
# ──────────────────────────────────────────────────────────────

async def check_y_report_contract(client) -> bool:
    """
    (Fixture: COMPLETED Simulation 생성)
      → POST /y-reports
      → mock TX (pending → processing → completed)
      → GET /jobs/{id}      (status=completed, result_id=yr_id)
      → GET /y-reports/{id} (status=COMPLETED, output_json+markdown 있음)
    """
    _header("Y_REPORT CONTRACT 확인")

    from app.db.session import get_async_session
    from app.repositories.simulation_repo import SimulationRepo
    from tasks.y_report_task import _tx1_processing, _tx2_completed

    fixture_sim_id: Optional[int] = None
    yr_id:          Optional[int] = None
    job_id:         Optional[int] = None

    try:
        # ── Fixture: COMPLETED Simulation 생성 ─────────────────
        async with get_async_session() as session:
            async with session.begin():
                sim = await SimulationRepo(session).create_completed(
                    store_source_id="smoke_test",
                    x_report_id=None,
                    selected_strategy_ids=[],
                    days=1,
                    seed=42,
                    output_json=_MOCK_SIM_OUTPUT,
                )
        fixture_sim_id = sim.id
        print(f"  [OK] Fixture COMPLETED Simulation {fixture_sim_id} 생성")

        # ── POST /y-reports ────────────────────────────────────
        with patch("tasks.y_report_task.generate_y_report.delay") as mock_delay:
            mock_delay.return_value = MagicMock(id="smoke-yr-celery")
            r = await client.post("/y-reports", json={"simulation_id": fixture_sim_id})

        assert r.status_code == 201, f"POST /y-reports 실패: {r.status_code} {r.text[:200]}"
        d = r.json()
        assert "job_id" in d and "y_report_id" in d, f"응답 필드 누락: {d}"
        assert d["status"] == "pending"
        yr_id  = d["y_report_id"]
        job_id = d["job_id"]
        print(f"  [OK] POST /y-reports → job_id={job_id}, y_report_id={yr_id}, status=pending")

        # ── Mock TX: pending → processing → completed ──────────
        mock_output = {
            **_MOCK_YR_OUTPUT,
            "service_meta": {
                **_MOCK_YR_OUTPUT["service_meta"],
                "simulation_id": fixture_sim_id,
            },
        }
        await _tx1_processing(job_id)
        await _tx2_completed(job_id, yr_id, mock_output, _MOCK_YR_MARKDOWN)

        # ── GET /jobs/{id} ─────────────────────────────────────
        r = await client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        job_data = r.json()
        _assert_job_fields(job_data)
        assert job_data["status"] == "completed",   f"job status 불일치: {job_data['status']}"
        assert job_data["result_id"] == yr_id,      f"result_id 불일치: {job_data['result_id']}"
        assert job_data["error_message"] is None
        print(f"  [OK] GET /jobs/{job_id} → status=completed, result_id={yr_id}")

        # ── GET /y-reports/{id} ────────────────────────────────
        r = await client.get(f"/y-reports/{yr_id}")
        assert r.status_code == 200, f"GET /y-reports/{yr_id} 실패: {r.status_code}"
        yr_data = r.json()
        assert yr_data["id"] == yr_id
        assert yr_data["status"] == "COMPLETED",    f"yr status 불일치: {yr_data['status']}"
        assert yr_data["output_json"] is not None,  "output_json 없음"
        assert yr_data["markdown"] is not None,     "markdown 없음"
        print(f"  [OK] GET /y-reports/{yr_id} → status=COMPLETED, output_json+markdown 있음")
        _warn_if_server_paths("GET /y-reports/{id}", yr_data)

        return True

    finally:
        if yr_id is not None and job_id is not None:
            await _del_yr_and_job(yr_id, job_id)
        if fixture_sim_id is not None:
            await _del_sim(fixture_sim_id)


# ──────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────

async def main() -> None:
    _header("프론트 연동 계약 검증 (in-process, mock TX)")
    print(f"  LOG_LEVEL={LOG_LEVEL}  SQL_ECHO={SQL_ECHO}")
    print("  Celery/Redis 없음 — task helper 직접 호출로 completed 상태 전환")

    from httpx import AsyncClient, ASGITransport
    from app.main import app as fastapi_app
    # app.main 임포트 시 setup_logging("DEBUG") 가 호출되어 로그 레벨을 덮어씀.
    # 스크립트 출력을 깔끔하게 유지하기 위해 사용자 요청 레벨로 복원한다.
    from app.core.logging import setup_logging as _setup_logging
    _setup_logging(LOG_LEVEL)

    checks = [
        ("JOB FIELDS",          check_job_fields),
        ("SIMULATION CONTRACT", check_simulation_contract),
        ("Y_REPORT CONTRACT",   check_y_report_contract),
    ]

    results: dict = {}
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        for name, fn in checks:
            try:
                results[name] = await fn(client)
            except Exception as e:
                import traceback
                print(f"\n  [FAIL] {name}: {type(e).__name__}: {e}")
                traceback.print_exc()
                results[name] = False

    _header("최종 결과")
    all_pass = True
    for name, ok in results.items():
        label = "PASS" if ok else "FAIL"
        print(f"  {name}: {label}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  FRONT_CONTRACT_OK")
        sys.exit(0)
    else:
        print("\n  SOME FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
