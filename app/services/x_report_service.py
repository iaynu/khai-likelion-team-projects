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
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import JobStatus
from app.models.x_report import XReport
from app.repositories.job_repo import JobRepo
from app.repositories.snapshot_repo import SnapshotRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.x_report_repo import XReportRepo
from app.services.llm_gpt_provider import generate_x_report_gpt
from app.services.llm_mock_provider import generate_x_report as _mock_generate

logger = logging.getLogger(__name__)

# XReport.status 값 — "completed"는 구 Celery 경로 호환용 (PART 3 완료 후 제거 가능)
_SUCCEEDED_STATUSES = {"SUCCEEDED", "completed"}

# feature_scores 키 → 레이더 축 한글명
_FEATURE_TO_RADAR = {
    "taste": "맛",
    "service": "서비스",
    "atmosphere": "분위기",
    "price_value": "가격",
    "cleanliness": "위생",
}


def _load_store_json(store_name: str) -> Optional[Dict]:
    """
    simulation/data/raw/split_by_store_id_ver5_/{store_name}.json 로드.
    파일이 없으면 None 반환.
    """
    try:
        from app.core.config import settings
        base = Path(settings.SIMULATION_ENGINE_PATH)
        path = base / "data" / "raw" / "split_by_store_id_ver5_" / f"{store_name}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug("store_json_load_failed: %s", exc)
    return None


def _radar_from_feature_scores(feature_scores: Dict) -> List[Dict]:
    """
    feature_scores (0-1 범위) → radar_data 리스트 (0-100 범위).
    JSON의 실제 데이터를 레이더 차트에 직접 반영한다.
    """
    result = []
    for key, subject in _FEATURE_TO_RADAR.items():
        info = feature_scores.get(key) or {}
        raw = info.get("score", 0) if isinstance(info, dict) else float(info or 0)
        avg_raw = info.get("avg_score", 0) if isinstance(info, dict) else 0
        result.append({
            "subject": subject,
            "A": round(raw * 100),
            "fullMark": 100,
            "avg": round(avg_raw * 100),  # 상권 평균 (참고용)
        })
    return result


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

    Persistence는 generate_or_get_x_report() 단일 진입점에 위임한다.
    이 함수는 Store/Snapshot 해석 + LLM 호출만 담당한다.
    """
    store_repo = StoreRepo(session)
    snap_repo = SnapshotRepo(session)
    xreport_repo = XReportRepo(session)

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

    store_id_val = str(store.id)
    snapshot_id_val = snapshot.id

    # ── early cache check (LLM 호출 전, 불필요한 비용 방지) ─────────────────
    existing = await xreport_repo.get_by_unique_key(store_id_val, snapshot_id_val, prompt_id)
    if existing and existing.status in _SUCCEEDED_STATUSES and not force_regenerate:
        logger.info(
            "x_report_cache_hit_precheck",
            extra={"extra_fields": {"x_report_id": existing.id}},
        )
        # job_id 없는 API 경로 — generate_or_get_x_report 경유하지 않아도 동일 결과
        return {"report": existing, "cached": True}

    # ── call merged input function ───────────────────────
    try:
        merged_input = await xreport_repo.get_merged_input(store_id_val, snapshot_id_val)
    except Exception as exc:
        logger.warning("merged_input_function_unavailable: %s", exc)
        merged_input = {"market_sales_review": None, "population": None, "store_id": store_id_val}

    # ── load store JSON (simulation data) ────────────────
    store_json = _load_store_json(store.store_name)
    precomputed_radar: Optional[List[Dict]] = None

    if store_json:
        feature_scores = (
            store_json.get("review_metrics", {}).get("feature_scores") or {}
        )
        if feature_scores:
            precomputed_radar = _radar_from_feature_scores(feature_scores)
        logger.info(
            "store_json_loaded",
            extra={"extra_fields": {"store": store.store_name, "has_radar": precomputed_radar is not None}},
        )

    # ── build store_data for GPT ──────────────────────────
    store_data: Dict = {
        "sector_name": store.sector_name or "",
        "area_code": store.area_code or "",
        "review_metrics": merged_input.get("market_sales_review") or {},
        "population_data": merged_input.get("population") or "정보 없음",
        "sales_metrics": "정보 없음",
    }
    if store_json:
        # 실제 JSON 데이터로 보강
        store_data.update({
            "review_metrics": store_json.get("review_metrics") or store_data["review_metrics"],
            "market_analysis": store_json.get("market_analysis") or {},
            "revenue_analysis": store_json.get("revenue_analysis") or "정보 없음",
            "customer_analysis": store_json.get("customer_analysis") or "정보 없음",
            "top_keywords": store_json.get("top_keywords") or [],
            "critical_feedback": store_json.get("critical_feedback") or [],
            "rag_context": store_json.get("rag_context") or "",
        })

    # ── call GPT LLM ─────────────────────────────────────
    try:
        llm_result = await generate_x_report_gpt(
            store.store_name, store_data, prompt_id, precomputed_radar=precomputed_radar
        )
        strategies_raw = []  # solutions stored in config_json.categories (P1 path)
    except Exception as exc:
        logger.warning("gpt_provider_failed_fallback_to_mock: %s", exc)
        llm_result = _mock_generate(merged_input, prompt_id)
        strategies_raw = llm_result["config_json"].get("strategies", [])

    # ── persist via unified entry point ─────────────────
    report, cached = await generate_or_get_x_report(
        session=session,
        store_id=store_id_val,
        snapshot_id=snapshot_id_val,
        prompt_id=prompt_id,
        report_payload={
            "content_text": llm_result["content_text"],
            "config_json": llm_result["config_json"],
            "model_meta": llm_result["model_meta"],
        },
        strategies_payload=strategies_raw,
        force_regenerate=force_regenerate,
        job_id=None,  # API 경로는 Celery job 없음
    )

    logger.info("x_report_generated", extra={"extra_fields": {
        "x_report_id": report.id,
        "cached": cached,
        "strategies_count": len(strategies_raw),
        "cost": llm_result["model_meta"].get("cost_usd"),
    }})

    return {"report": report, "cached": cached}


# ──────────────────────────────────────────────────────────────────────────────
# 단일 서비스 진입점 — Celery Task / API Router 공용
# ──────────────────────────────────────────────────────────────────────────────

async def generate_or_get_x_report(
    session: AsyncSession,
    store_id: str,
    snapshot_id: int,
    prompt_id: str,
    report_payload: Dict,
    strategies_payload: List[Dict],
    force_regenerate: bool = False,
    job_id: Optional[int] = None,
) -> Tuple[XReport, bool]:
    """단일 진입점: X-Report 신규 생성 / 캐시 반환 / 재생성을 통합 처리한다.

    5가지 정책을 모두 이 함수 하나에서 구현한다.

    Args:
        session:             AsyncSession (호출 시점에 활성 트랜잭션 없어야 함)
        store_id:            내부 Store.id (str UUID)
        snapshot_id:         내부 Snapshot.id (int)
        prompt_id:           프롬프트 식별자
        report_payload:      {content_text, config_json, model_meta}
        strategies_payload:  정확히 9개의 strategy dict 리스트
        force_regenerate:    True 이면 기존 SUCCEEDED 행도 덮어씀
        job_id:              제공 시 Job.result_id / Job.status 를 함께 업데이트

    Returns:
        (XReport ORM 객체, cached: bool)
        cached=True  → 기존 SUCCEEDED 결과를 그대로 반환 (GPT 미호출)
        cached=False → 신규 생성 또는 재생성

    Raises:
        RuntimeError: IntegrityError 후 기존 행을 찾을 수 없는 비정상 상황

    # ── PART 3 Celery Task 호출 예시 ──────────────────────────────
    # async with get_async_session() as session:
    #     report, cached = await generate_or_get_x_report(
    #         session=session,
    #         store_id=store.id,
    #         snapshot_id=snapshot.id,
    #         prompt_id=prompt_id,
    #         report_payload={
    #             "content_text": report_text,
    #             "config_json": {"analysis_log": analysis_result},
    #             "model_meta": model_meta,
    #         },
    #         strategies_payload=strategies,   # extract_solutions() 결과 9개
    #         force_regenerate=False,
    #         job_id=job_id,
    #     )
    #
    # ── PART 3 API Router 호출 예시 ──────────────────────────────
    # report, cached = await generate_or_get_x_report(
    #     session=db,
    #     store_id=store.id,
    #     snapshot_id=snapshot.id,
    #     prompt_id=body.prompt_id,
    #     report_payload={...},
    #     strategies_payload=strategies,
    #     force_regenerate=body.force_regenerate,
    #     job_id=job_id,
    # )
    """
    xreport_repo = XReportRepo(session)
    strat_repo = StrategyRepo(session)
    job_repo = JobRepo(session)

    # ── Step 1: 기존 행 조회 (상태 필터 없음) ────────────────────────────────
    existing = await xreport_repo.get_by_unique_key(store_id, snapshot_id, prompt_id)

    # ── Step 2: 캐시 히트 경로 ────────────────────────────────────────────────
    # 정책 1: SUCCEEDED + force_regenerate=False → 기존 결과 반환
    # 정책 3: FAILED 는 이 블록에 들어오지 않음 (재생성 필요)
    if existing is not None and existing.status in _SUCCEEDED_STATUSES and not force_regenerate:
        logger.info(
            "x_report_cache_hit",
            extra={"extra_fields": {"x_report_id": existing.id, "job_id": job_id}},
        )
        # ── [TX BOUNDARY] 캐시 히트: job result_id 업데이트만 커밋 ─────────────
        # 정책 4: 캐시 히트에서도 반드시 job.result_id = existing.id 기록
        if job_id is not None:
            await job_repo.update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=existing.id,
                progress=100,
            )
            await session.commit()
        return (existing, True)

    # ── Step 3: Upsert 경로 (기존 행 있음: FAILED 또는 force_regenerate=True) ─
    # 정책 2: Unique 키 유지 + 기존 행 UPDATE (INSERT 금지)
    # 정책 3: FAILED 는 force_regenerate 여부와 무관하게 여기서 처리
    if existing is not None:
        reason = "force_regenerate" if force_regenerate else "failed_retry"
        logger.info(
            "x_report_upsert",
            extra={"extra_fields": {"x_report_id": existing.id, "reason": reason, "job_id": job_id}},
        )
        # ── [TX BOUNDARY] Upsert: report UPDATE + strategies DELETE+INSERT + job UPDATE ──
        # 세 작업이 단일 암시적 트랜잭션 안에 묶임 → 부분 반영 불가
        await xreport_repo.update_report_fields(
            x_report_id=existing.id,
            content_text=report_payload["content_text"],
            config_json=report_payload.get("config_json"),
            model_meta=report_payload.get("model_meta"),
        )
        # 정책 2: strategies 9개 교체 (delete + bulk insert)
        await strat_repo.replace_all(existing.id, strategies_payload)
        # 정책 4: job result 매핑
        if job_id is not None:
            await job_repo.update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=existing.id,
                progress=100,
            )
        await session.commit()
        await session.refresh(existing)
        return (existing, False)

    # ── Step 4: 신규 INSERT 경로 ─────────────────────────────────────────────
    try:
        report = XReport(
            store_id=store_id,
            snapshot_id=snapshot_id,
            prompt_id=prompt_id,
            status="SUCCEEDED",
            content_text=report_payload["content_text"],
            config_json=report_payload.get("config_json"),
            model_meta=report_payload.get("model_meta"),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(report)
        # flush 시점에 UniqueConstraint 위반 → IntegrityError 발생 가능
        await session.flush()
        await session.refresh(report)

        # ── [TX BOUNDARY] INSERT: report + strategies + job 단일 트랜잭션 ────────
        await strat_repo.bulk_create(report.id, strategies_payload)
        # 정책 4: job result 매핑
        if job_id is not None:
            await job_repo.update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=report.id,
                progress=100,
            )
        await session.commit()

        logger.info(
            "x_report_created",
            extra={"extra_fields": {"x_report_id": report.id, "job_id": job_id}},
        )
        return (report, False)

    # ── Step 5: IntegrityError 흡수 (정책 5: 동시성 중복 실행 처리) ─────────────
    except IntegrityError:
        # async with session.begin() 없이 flush에서 발생한 경우이므로 명시 rollback 필요
        await session.rollback()

        # 롤백 후 기존 행 재조회 (concurrent insert 가 커밋한 행)
        existing = await xreport_repo.get_by_unique_key(store_id, snapshot_id, prompt_id)

        if existing is None:
            # IntegrityError 가 발생했는데 행을 찾을 수 없음 — 비정상 상황
            raise RuntimeError(
                "[UNKNOWN_ERROR] IntegrityError during XReport insert "
                f"but no existing row found for store_id={store_id}, "
                f"snapshot_id={snapshot_id}, prompt_id={prompt_id}"
            )

        if existing.status in _SUCCEEDED_STATUSES:
            # 정책 5: 동시 실행에서 상대방이 SUCCEEDED 로 커밋 완료 → 캐시 히트로 흡수
            logger.warning(
                "x_report_concurrent_insert_absorbed",
                extra={"extra_fields": {"x_report_id": existing.id, "job_id": job_id}},
            )
            # ── [TX BOUNDARY] 흡수된 캐시 히트: job result_id 업데이트만 커밋 ──
            if job_id is not None:
                await job_repo.update_status(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    result_id=existing.id,
                    progress=100,
                )
                await session.commit()
            return (existing, True)

        else:
            # 동시 실행이 FAILED 행을 남긴 경우 → Upsert 재시도
            logger.warning(
                "x_report_concurrent_insert_failed_row_upsert",
                extra={"extra_fields": {"x_report_id": existing.id, "job_id": job_id}},
            )
            # ── [TX BOUNDARY] 재시도 Upsert: report UPDATE + strategies + job ──
            await xreport_repo.update_report_fields(
                x_report_id=existing.id,
                content_text=report_payload["content_text"],
                config_json=report_payload.get("config_json"),
                model_meta=report_payload.get("model_meta"),
            )
            await strat_repo.replace_all(existing.id, strategies_payload)
            if job_id is not None:
                await job_repo.update_status(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    result_id=existing.id,
                    progress=100,
                )
            await session.commit()
            await session.refresh(existing)
            return (existing, False)
