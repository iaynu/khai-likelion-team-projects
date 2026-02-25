"""
SimulationAdapter

시뮬레이션 엔진(scripts/run_generative_simulation.py)을 감싸
백엔드에서 호출 가능한 인터페이스를 제공합니다.

완료 조건:
- 동일 input -> 항상 JSON-serializable dict 반환
- json.dumps(output, ensure_ascii=False) 에러 없음

공개 인터페이스:
  SimulationAdapter      : 단일 run_simulation() 래퍼 (class)
  run_full_pipeline()    : before/after 전체 파이프라인 (function, 기존 유지)
"""
import asyncio
import json
import random
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.engine.path_setup import ensure_engine_path
from app.utils.serialization import to_jsonable


def _df_to_summary(df, target_store: str) -> Dict[str, Any]:
    """
    결과 DataFrame -> JSON-serializable 요약 dict.
    pandas/numpy 타입을 Python 기본 타입으로 변환합니다.
    """
    visit_df = df[df["decision"] == "visit"]
    total = int(len(df))
    visits = int(len(visit_df))

    summary: Dict[str, Any] = {
        "total_events": total,
        "visit_count": visits,
        "visit_rate_pct": round(visits / total * 100, 2) if total else 0.0,
        "avg_rating": None,
        "target_visit_count": 0,
        "top10_stores": [],
    }

    if visits > 0:
        rating_col = visit_df["rating"].dropna()
        if not rating_col.empty:
            summary["avg_rating"] = round(float(rating_col.mean()), 3)

        target_visits = visit_df[visit_df["visited_store"] == target_store]
        summary["target_visit_count"] = int(len(target_visits))

        top10 = visit_df["visited_store"].value_counts().head(10)
        summary["top10_stores"] = [
            {"store": str(s), "count": int(c)} for s, c in top10.items()
        ]

    return summary


# ──────────────────────────────────────────────────────────────
# SimulationAdapter (신규)
# ──────────────────────────────────────────────────────────────

class SimulationAdapter:
    """
    단일 run_simulation() 호출을 캡슐화하는 Adapter.

    run_full_pipeline()이 before/after + X_to_Sim 전체 오케스트레이션을 담당하는 반면,
    SimulationAdapter.run()은 단일 run_simulation() 호출만 래핑하여
    항상 JSON-serializable dict를 반환하는 계약을 보장합니다.

    반환 구조:
        {
          "summary": dict,   # 프론트에 바로 보여줄 핵심 수치
          "raw":     dict,   # 전체 레코드 리스트 (JSON-serializable)
          "meta":    dict,   # 버전/파라미터/런타임
        }
    """

    async def run(
        self,
        *,
        agents,
        global_store,
        settings,
        days: int,
        max_concurrent_llm_calls: int = 5,
    ) -> Dict[str, Any]:
        """
        run_simulation()을 호출하고 JSON-serializable dict를 반환합니다.

        Args:
            agents: GenerativeAgent 목록
            global_store: GlobalStore 인스턴스
            settings: simulation config (get_sim_settings() 결과)
            days: 시뮬레이션 기간 (일)
            max_concurrent_llm_calls: 동시 LLM 호출 수 상한

        Returns:
            {
                "summary": {"total_events", "visit_count", "visit_rate_pct",
                            "avg_rating", "top10_stores"},
                "raw":     {"records": list[dict]},
                "meta":    {"days", "agent_count", "max_concurrent_llm_calls",
                            "elapsed_sec"},
            }

        Raises:
            RuntimeError: SIMULATION_ENGINE_PATH 미설정 또는 엔진 import 실패
        """
        ensure_engine_path()
        from scripts.run_generative_simulation import run_simulation

        t0 = time.perf_counter()
        df = await run_simulation(
            agents, global_store, settings, days, max_concurrent_llm_calls
        )
        elapsed = round(time.perf_counter() - t0, 2)

        # -- summary: 프론트에 바로 보여줄 핵심 수치 --------------------
        visit_df = df[df["decision"] == "visit"]
        total = int(len(df))
        visits = int(len(visit_df))

        summary: Dict[str, Any] = {
            "total_events": total,
            "visit_count": visits,
            "visit_rate_pct": round(visits / total * 100, 2) if total else 0.0,
            "avg_rating": None,
            "top10_stores": [],
        }
        if visits > 0:
            rating_col = visit_df["rating"].dropna()
            if not rating_col.empty:
                summary["avg_rating"] = round(float(rating_col.mean()), 3)
            top10 = visit_df["visited_store"].value_counts().head(10)
            summary["top10_stores"] = [
                {"store": str(s), "count": int(c)} for s, c in top10.items()
            ]

        # -- raw: 전체 결과를 가능한 범위에서 직렬화 (numpy 타입 제거) ---
        raw: Dict[str, Any] = {
            "records": to_jsonable(df.to_dict(orient="records"))
        }

        # -- meta: 버전/파라미터/런타임 ---------------------------------
        meta: Dict[str, Any] = {
            "days": days,
            "agent_count": len(agents),
            "max_concurrent_llm_calls": max_concurrent_llm_calls,
            "elapsed_sec": elapsed,
        }

        result: Dict[str, Any] = {"summary": summary, "raw": raw, "meta": meta}
        json.dumps(result, ensure_ascii=False)  # 직렬화 계약 검증
        return result


# ──────────────────────────────────────────────────────────────
# run_full_pipeline (기존 — 하위 호환 유지)
# ──────────────────────────────────────────────────────────────

async def run_full_pipeline(
    store_source_id: str,
    selected_strategy_ids: List[str],
    days: int = 7,
    seed: int = 42,
    agent_count: int = 160,
    max_concurrent_llm_calls: int = 5,
    api_key: str = "",
) -> Dict[str, Any]:
    """
    전략 전/후 시뮬레이션 전체 파이프라인 실행.

    Args:
        store_source_id: 타겟 매장명 (예: "돼지야")
        selected_strategy_ids: X_to_Sim 형식 전략 ID 목록 (예: ["S1_A", "S2_A"])
                               빈 리스트이면 전략 미적용 (baseline only)
        days: 시뮬레이션 기간 (일)
        seed: 랜덤 시드 (before/after 동일 에이전트 구성 보장)
        agent_count: 에이전트 수 (최대 160)
        max_concurrent_llm_calls: 동시 LLM 호출 수 상한 (기본 5)
        api_key: LLM API 키 (전략 적용 시 필요)

    Returns:
        JSON-serializable dict:
        {
            "before": { visit_count, avg_rating, target_visit_count, top10_stores, output_dir },
            "after":  { ... },  # selected_strategy_ids가 빈 경우 None
            "target_store": str,
        }

    Raises:
        RuntimeError: SIMULATION_ENGINE_PATH 미설정 또는 엔진 import 실패
        Exception: 시뮬레이션 내부 오류
    """
    engine_path = ensure_engine_path()

    # 시뮬레이션 엔진 import (경로 추가 후)
    from config import get_settings as get_sim_settings
    from src.data_layer.global_store import GlobalStore, get_global_store
    from scripts.run_generative_simulation import run_simulation, generate_agents

    sim_settings = get_sim_settings()

    # 시드 고정
    random.seed(seed)
    np.random.seed(seed)

    output_base = engine_path / "data" / "output"
    before_dir = output_base / f"{store_source_id}_before"
    after_dir = output_base / f"{store_source_id}_after"
    store_json_dir = engine_path / "data" / "raw" / "split_by_store_id_ver5_"

    # Simulation 1: Before
    print(f"[SimAdapter] 시뮬레이션 1/2 시작 (before) - {store_source_id}")
    GlobalStore.reset_instance()
    global_store_before = get_global_store()
    global_store_before.load_from_json_files(store_json_dir)

    agents = generate_agents(agent_count)
    random.seed(seed)
    np.random.seed(seed)

    df_before = await run_simulation(
        agents, global_store_before, sim_settings, days, max_concurrent_llm_calls
    )

    # 결과 저장 (before)
    before_dir.mkdir(parents=True, exist_ok=True)
    df_before.to_csv(before_dir / "simulation_result.csv", index=False, encoding="utf-8-sig")
    visit_before = df_before[df_before["decision"] == "visit"]
    visit_before.to_csv(before_dir / "visit_log.csv", index=False, encoding="utf-8-sig")
    global_store_before.save_to_json(before_dir / "store_ratings.json")
    agents_data = [a.to_dict() for a in agents]
    (before_dir / "agents_final.json").write_text(
        json.dumps(agents_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    before_summary = _df_to_summary(df_before, store_source_id)
    before_summary["output_dir"] = str(before_dir)

    # 전략 적용 (X_to_Sim Bridge)
    after_summary: Optional[Dict[str, Any]] = None

    if selected_strategy_ids:
        x_report_md = engine_path / "data" / "raw" / "전략 md 파일들" / f"{store_source_id}_report.md"
        store_json_path = store_json_dir / f"{store_source_id}.json"
        applied_json_path = store_json_dir / f"{store_source_id}_applied.json"

        if x_report_md.exists() and store_json_path.exists():
            print(f"[SimAdapter] 전략 적용 중 (X_to_Sim): {selected_strategy_ids}")
            from X_to_Sim import apply_x_report_strategy_async

            await apply_x_report_strategy_async(
                store_json_path=str(store_json_path),
                x_report_path=str(x_report_md),
                selected_strategy_ids=selected_strategy_ids,
                api_key=api_key,
                output_path=str(applied_json_path),
            )

            applied_store_dir = engine_path / "data" / "raw" / f"split_by_store_id_after_{store_source_id}"
            if applied_store_dir.exists():
                shutil.rmtree(applied_store_dir)
            shutil.copytree(store_json_dir, applied_store_dir)
            shutil.copy(applied_json_path, applied_store_dir / f"{store_source_id}.json")
        else:
            print(f"[SimAdapter] X-Report .md 없음 - 전략 미적용으로 after 실행")
            applied_store_dir = store_json_dir  # fallback: 원본 그대로

        # Simulation 2: After
        print(f"[SimAdapter] 시뮬레이션 2/2 시작 (after) - {store_source_id}")
        GlobalStore.reset_instance()
        global_store_after = get_global_store()
        global_store_after.load_from_json_files(applied_store_dir)

        random.seed(seed)
        np.random.seed(seed)
        agents_after = generate_agents(agent_count)

        random.seed(seed)
        np.random.seed(seed)
        df_after = await run_simulation(
            agents_after, global_store_after, sim_settings, days, max_concurrent_llm_calls
        )

        # 결과 저장 (after)
        after_dir.mkdir(parents=True, exist_ok=True)
        df_after.to_csv(after_dir / "simulation_result.csv", index=False, encoding="utf-8-sig")
        visit_after = df_after[df_after["decision"] == "visit"]
        visit_after.to_csv(after_dir / "visit_log.csv", index=False, encoding="utf-8-sig")
        global_store_after.save_to_json(after_dir / "store_ratings.json")
        agents_after_data = [a.to_dict() for a in agents_after]
        (after_dir / "agents_final.json").write_text(
            json.dumps(agents_after_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        after_summary = _df_to_summary(df_after, store_source_id)
        after_summary["output_dir"] = str(after_dir)

        # 임시 디렉토리 정리
        if selected_strategy_ids and applied_store_dir != store_json_dir:
            shutil.rmtree(applied_store_dir, ignore_errors=True)
        if applied_json_path.exists():
            applied_json_path.unlink()

    return {
        "target_store": store_source_id,
        "before": before_summary,
        "after": after_summary,
    }
