"""
Engine Import Test

uvicorn/Celery 없이 로컬에서 바로 실행:
    python scripts/test_import_engines.py

성공 시: IMPORT_OK 출력
실패 시: 상세 에러 출력 후 exit(1)

검증 대상:
  1. SIMULATION_ENGINE_PATH 로드 (app.core.config.settings)
  2. ensure_engine_path() -- sim root -> sys.path 주입
  3. from scripts.run_generative_simulation import run_simulation
  4. from sim_to_y import ComparisonReportGenerator
"""
import io
import sys
import traceback
from pathlib import Path

# Windows cp949 콘솔에서 한글/특수문자 깨짐 방지
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 백엔드 루트를 sys.path에 추가 (app.* 모듈 import용)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


def _check(label: str, fn):
    """fn() 실행 후 성공/실패를 출력. 실패 시 None 반환."""
    try:
        result = fn()
        print(f"  [OK]   {label}")
        return result
    except Exception as exc:
        print(f"  [FAIL] {label}")
        print(f"         {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


def main():
    print("=" * 56)
    print("  Engine Import Test")
    print("=" * 56)

    failed = 0

    # -- Step 1: SIMULATION_ENGINE_PATH 확인 ----------------------
    def load_config():
        from app.core.config import settings
        path = settings.SIMULATION_ENGINE_PATH
        if not path:
            raise RuntimeError(
                "SIMULATION_ENGINE_PATH 가 비어있습니다. "
                "backend .env 에 SIMULATION_ENGINE_PATH=C:/Users/user/simulation 추가"
            )
        return path

    engine_path_raw = _check("SIMULATION_ENGINE_PATH 로드", load_config)
    if engine_path_raw is None:
        failed += 1

    # -- Step 2: ensure_engine_path() -----------------------------
    engine_path = _check(
        "ensure_engine_path() -- sim root -> sys.path",
        lambda: __import__(
            "app.engine.path_setup", fromlist=["ensure_engine_path"]
        ).ensure_engine_path(),
    )
    if engine_path is None:
        failed += 1

    # -- Step 3: run_simulation import ----------------------------
    run_simulation = _check(
        "from scripts.run_generative_simulation import run_simulation",
        lambda: __import__(
            "scripts.run_generative_simulation",
            fromlist=["run_simulation"],
        ).run_simulation,
    )
    if run_simulation is None:
        failed += 1

    # -- Step 4: ComparisonReportGenerator import -----------------
    ComparisonReportGenerator = _check(
        "from sim_to_y import ComparisonReportGenerator",
        lambda: __import__(
            "sim_to_y", fromlist=["ComparisonReportGenerator"]
        ).ComparisonReportGenerator,
    )
    if ComparisonReportGenerator is None:
        failed += 1

    # -- 결과 -----------------------------------------------------
    print("=" * 56)
    if failed == 0:
        print("IMPORT_OK")
        sys.exit(0)
    else:
        print(f"FAILED ({failed}/4 항목 실패)")
        sys.exit(1)


if __name__ == "__main__":
    main()
