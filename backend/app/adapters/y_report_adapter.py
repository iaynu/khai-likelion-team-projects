"""
YReportAdapter

sim_to_y.py의 ComparisonReportGenerator를 감싸
백엔드에서 호출 가능한 인터페이스를 제공합니다.

완료 조건:
- 동일 input -> 항상 JSON-serializable dict 반환
- json.dumps(output, ensure_ascii=False) 에러 없음
- 오래 걸리므로 asyncio.to_thread() 사용

공개 인터페이스:
  YReportAdapter  : simulation_output dict 기반 래퍼 (class)
  generate()      : 경로 직접 지정 방식 (function, 기존 유지)
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.engine.path_setup import ensure_engine_path
from app.utils.serialization import to_jsonable


def _run_comparison_report(
    target_store: str,
    before_dir: str,
    after_dir: str,
    output_dir: str,
) -> Dict[str, Any]:
    """
    ComparisonReportGenerator 동기 실행 (to_thread에서 호출).

    Returns:
        {
            "markdown": str,           # 전체 마크다운 보고서
            "metrics": dict,           # metrics_summary (숫자 지표)
            "report_path": str,        # 저장된 .md 파일 경로
        }
    """
    ensure_engine_path()

    from sim_to_y import ComparisonReportGenerator

    generator = ComparisonReportGenerator(
        target_store=target_store,
        before_dir=Path(before_dir),
        after_dir=Path(after_dir),
        output_dir=Path(output_dir),
    )

    report_path: Path = generator.generate_all()

    # 마크다운 본문 읽기
    markdown_text = ""
    if report_path and report_path.exists():
        markdown_text = report_path.read_text(encoding="utf-8")

    # metrics_summary: numpy/pandas 타입 -> Python 기본 타입 변환
    metrics = to_jsonable(generator.metrics_summary)

    return {
        "markdown": markdown_text,
        "metrics": metrics,
        "report_path": str(report_path) if report_path else "",
    }


# ──────────────────────────────────────────────────────────────
# YReportAdapter (신규)
# ──────────────────────────────────────────────────────────────

class YReportAdapter:
    """
    ComparisonReportGenerator 클래스 래퍼.

    simulation_output dict (run_full_pipeline 출력 형식)에서
    before/after 경로를 추출하여 Y-Report를 생성합니다.

    반환 구조:
        {
          "structured": dict,  # metrics_summary (section/table 등 수치 지표)
          "markdown":   str,   # 전체 마크다운 보고서
          "meta":       dict,  # 파라미터 정보
        }
    """

    async def generate(
        self,
        *,
        simulation_output: Dict[str, Any],
        x_report: Dict[str, Any],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        전략 전/후 비교 보고서 생성.

        Args:
            simulation_output: run_full_pipeline() 반환값 또는 동일 형식의 dict.
                               {"target_store": str,
                                "before": {"output_dir": str, ...},
                                "after":  {"output_dir": str, ...}} 형식.
            x_report: X-Report 데이터 (맥락 정보 - ComparisonReportGenerator에 직접 전달하지 않음)
            options:  추가 옵션 dict
                - before_dir:   simulation_output["before"]["output_dir"] 오버라이드
                - after_dir:    simulation_output["after"]["output_dir"] 오버라이드
                - target_store: 매장명 오버라이드
                - output_dir:   보고서 저장 경로 (기본: after_dir/y_report/)

        Returns:
            {
                "structured": dict,  # metrics_summary (section별 수치 지표)
                "markdown":   str,   # 전체 마크다운 보고서
                "meta":       dict,  # target_store, before_dir, after_dir, report_path
            }

        Raises:
            ValueError: before_dir 또는 after_dir 확인 불가
            RuntimeError: SIMULATION_ENGINE_PATH 미설정
        """
        # 경로 추출: options 우선, 없으면 simulation_output에서
        before_dir: str = (
            options.get("before_dir")
            or simulation_output.get("before", {}).get("output_dir", "")
        )
        after_dir: str = (
            options.get("after_dir")
            or simulation_output.get("after", {}).get("output_dir", "")
        )
        target_store: str = (
            options.get("target_store")
            or simulation_output.get("target_store", "")
        )

        if not before_dir or not after_dir:
            raise ValueError(
                "[MISSING_DIR] before_dir 또는 after_dir 가 없습니다. "
                f"simulation_output={json.dumps(simulation_output, ensure_ascii=False)[:200]}"
            )

        output_dir: str = options.get("output_dir") or str(Path(after_dir) / "y_report")

        # 동기 함수를 별도 스레드에서 실행 (블로킹 방지)
        raw = await asyncio.to_thread(
            _run_comparison_report,
            target_store,
            before_dir,
            after_dir,
            output_dir,
        )

        result: Dict[str, Any] = {
            "structured": raw.get("metrics", {}),
            "markdown": raw.get("markdown", ""),
            "meta": {
                "target_store": target_store,
                "before_dir": before_dir,
                "after_dir": after_dir,
                "report_path": raw.get("report_path", ""),
                "x_report_keys": list(x_report.keys()) if x_report else [],
            },
        }

        json.dumps(result, ensure_ascii=False)  # 직렬화 계약 검증
        return result


# ──────────────────────────────────────────────────────────────
# generate() (기존 — 하위 호환 유지)
# ──────────────────────────────────────────────────────────────

async def generate(
    target_store: str,
    before_dir: str,
    after_dir: str,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    전략 전/후 비교 보고서 생성 (비동기 진입점).

    Args:
        target_store: 분석 대상 매장명 (예: "돼지야")
        before_dir: 전략 전 시뮬레이션 결과 디렉토리 경로
        after_dir: 전략 후 시뮬레이션 결과 디렉토리 경로
        output_dir: 보고서 저장 디렉토리 (None이면 after_dir/y_report/ 사용)

    Returns:
        JSON-serializable dict:
        {
            "markdown": str,
            "metrics": dict,
            "report_path": str,
        }

    Raises:
        RuntimeError: SIMULATION_ENGINE_PATH 미설정
        FileNotFoundError: before_dir / after_dir 없음
        Exception: 보고서 생성 내부 오류
    """
    if not output_dir:
        output_dir = str(Path(after_dir) / "y_report")

    # 동기 함수를 별도 스레드에서 실행 (블로킹 방지)
    result = await asyncio.to_thread(
        _run_comparison_report,
        target_store,
        before_dir,
        after_dir,
        output_dir,
    )

    # 최종 직렬화 검증
    json.dumps(result, ensure_ascii=False)  # 실패 시 여기서 예외 발생
    return result
