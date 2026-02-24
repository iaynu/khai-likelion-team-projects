"""
Simulation Engine 경로 설정

백엔드에서 simulation 프로젝트 모듈을 import 할 수 있도록
sys.path에 engine 경로를 추가합니다.

사용법:
    from app.engine.path_setup import ensure_engine_path
    ensure_engine_path()
    # 이후 simulation 모듈 import 가능
    from scripts.run_generative_simulation import run_simulation
    from sim_to_y import ComparisonReportGenerator
"""
import sys
from pathlib import Path

_initialized = False


def ensure_engine_path() -> Path:
    """
    SIMULATION_ENGINE_PATH를 sys.path에 추가.
    중복 추가 방지를 위해 한 번만 실행됩니다.

    Returns:
        engine_path: simulation 프로젝트 루트 Path 객체

    Raises:
        RuntimeError: SIMULATION_ENGINE_PATH가 설정되지 않았거나 존재하지 않는 경우
    """
    global _initialized

    from app.core.config import settings

    raw_path = settings.SIMULATION_ENGINE_PATH
    if not raw_path:
        raise RuntimeError(
            "SIMULATION_ENGINE_PATH가 설정되지 않았습니다. "
            "backend .env 에 SIMULATION_ENGINE_PATH=C:/Users/user/simulation 를 추가하세요."
        )

    engine_path = Path(raw_path).resolve()
    if not engine_path.exists():
        raise RuntimeError(
            f"SIMULATION_ENGINE_PATH 경로가 존재하지 않습니다: {engine_path}"
        )

    path_str = str(engine_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

    _initialized = True
    return engine_path
