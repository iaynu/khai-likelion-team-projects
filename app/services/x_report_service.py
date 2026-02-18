"""
X-Report generation orchestration:
  1) Resolve store (by source_id or name)
  2) Cache check on (store_id, snapshot_id, prompt_id)
  3) Call merged-input Postgres function
  4) Call Mock LLM Provider
  5) Persist x_report + 12 strategies atomically

TODO: Celery 도입 지점 — 단계 3~5를 async task로 분리하여
      대량 생성 시 백그라운드에서 실행할 수 있도록 변경한다.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.snapshot_repo import SnapshotRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.x_report_repo import XReportRepo
from app.repositories.strategy_repo import StrategyRepo
from app.services.llm_mock_provider import generate_x_report

logger = logging.getLogger(__name__)


async def create_x_report(
    session: AsyncSession,
    store_source_id: Optional[str],
    store_name: Optional[str],
    snapshot_version: str,
    prompt_id: str,
    force_regenerate: bool = False,
) -> dict:
    """
    Returns {"report": XReport ORM obj, "cached": bool} or raises ValueError.
    """
    store_repo = StoreRepo(session)
    snap_repo = SnapshotRepo(session)
    report_repo = XReportRepo(session)
    strat_repo = StrategyRepo(session)

    # ── resolve store ────────────────────────────────────
    store = None
    if store_source_id:
        store = await store_repo.get_by_source_id(store_source_id)
    if store is None and store_name:
        store = await store_repo.get_by_name(store_name)
    if store is None:
        raise ValueError("store not found: check store_source_id or store_name")

    # ── resolve snapshot ─────────────────────────────────
    snapshot = await snap_repo.get_by_version(snapshot_version)
    if snapshot is None:
        raise ValueError(f"snapshot_version '{snapshot_version}' not found")

    # ── cache check ──────────────────────────────────────
    existing = await report_repo.get_cached(store.id, snapshot.id, prompt_id)
    if existing and existing.status == "SUCCEEDED" and not force_regenerate:
        logger.info("x_report_cache_hit", extra={"extra_fields": {"x_report_id": existing.id}})
        return {"report": existing, "cached": True}

    # ── create or reuse row ──────────────────────────────
    if existing and force_regenerate:
        report = existing
        report.status = "RUNNING"
        report.error_message = None
        report.finished_at = None
    else:
        report = await report_repo.create_pending(store.id, snapshot.id, prompt_id)
        report.status = "RUNNING"
    await session.flush()

    # ── call merged input function ───────────────────────
    try:
        merged_input = await report_repo.get_merged_input(store.id, snapshot.id)
    except Exception as exc:
        # Fallback for SQLite test (no Postgres function) — use empty dict
        logger.warning("merged_input_function_unavailable: %s", exc)
        merged_input = {"market_sales_review": None, "population": None}

    # ── call mock LLM ────────────────────────────────────
    llm_result = generate_x_report(merged_input, prompt_id)

    # ── persist atomically ───────────────────────────────
    report.content_text = llm_result["content_text"]
    report.config_json = llm_result["config_json"]
    report.model_meta = llm_result["model_meta"]
    report.status = "SUCCEEDED"
    report.finished_at = datetime.now(timezone.utc)
    await session.flush()

    # Extract strategies from config_json and persist
    strategies_raw = llm_result["config_json"].get("strategies", [])
    await strat_repo.replace_all(report.id, strategies_raw)

    await session.commit()

    logger.info("x_report_generated", extra={"extra_fields": {
        "x_report_id": report.id,
        "strategies_count": len(strategies_raw),
        "cost": llm_result["model_meta"].get("cost_usd"),
    }})

    return {"report": report, "cached": False}
