"""
X-Report 생성 Celery Task
GPT 기반 X-Report 생성 후 DB 직접 저장
"""
import os
import json
import asyncio
import requests
import traceback
from typing import Dict, Any, List, Optional
from celery import Task
from openai import OpenAI
import openai  # For exceptions

from celery_app import celery_app
from app.core.config import settings

# DB 직접 접근을 위한 imports
from app.db.session import get_async_session
from app.repositories.job_repo import JobRepo
from app.repositories.x_report_repo import XReportRepo
from app.repositories.strategy_repo import StrategyRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.snapshot_repo import SnapshotRepo
from app.models.job import JobStatus


# ──────────────────────────────────────────────────────────
# 에러 처리 유틸리티
# ──────────────────────────────────────────────────────────

def format_error_message(
    error_code: str,
    message: str,
    context: Optional[str] = None
) -> str:
    """
    에러 메시지 표준 포맷

    형식: "[{ERROR_CODE}] {message} | Context: {context}"

    Args:
        error_code: 에러 코드 (예: "STRATEGY_PARSE_COUNT_MISMATCH")
        message: 핵심 메시지 (최대 500자)
        context: 추가 컨텍스트 - report_text 일부, 스택 트레이스 등 (최대 500자)

    Returns:
        구조화된 에러 메시지 (최대 1000자)

    Examples:
        >>> format_error_message("PARSE_ERROR", "Invalid format", "sample text...")
        "[PARSE_ERROR] Invalid format | Context: sample text..."
    """
    # 메시지 길이 제한
    message = message[:500]

    # 기본 포맷
    formatted = f"[{error_code}] {message}"

    # 컨텍스트 추가
    if context:
        context = context[:500]
        formatted += f" | Context: {context}"

    # 최종 길이 제한
    return formatted[:1000]


# ──────────────────────────────────────────────────────────
# DB 직접 접근 Async 헬퍼 함수들
# ──────────────────────────────────────────────────────────

async def _update_job_processing_async(job_id: int) -> None:
    """
    [Transaction 1] Job 상태를 PROCESSING으로 업데이트 (독립 트랜잭션)

    Args:
        job_id: Job ID

    Note:
        GPT 호출 전에 실행되어 사용자에게 작업 시작을 알립니다.
    """
    async with get_async_session() as session:
        async with session.begin():
            job_repo = JobRepo(session)
            await job_repo.update_status(
                job_id=job_id,
                status=JobStatus.PROCESSING,
                progress=10,
            )
    print(f"[TX1-COMMIT] Job {job_id} status → PROCESSING")


async def _save_report_async(
    job_id: int,
    store_source_id: str,
    snapshot_version: str,
    prompt_id: str,
    content_text: str,
    strategies: List[Dict],
    analysis_log: str,
    model_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """
    [Transaction 2] XReport + Strategies + Job COMPLETED 원자적 저장 (단일 트랜잭션)

    Args:
        job_id: Job ID
        store_source_id: 매장 소스 ID
        snapshot_version: 스냅샷 버전
        prompt_id: 프롬프트 ID
        content_text: X-Report 본문
        strategies: 9개 전략 리스트
        analysis_log: GPT 분석 로그
        model_meta: GPT 응답 메타데이터 (model, request_id, tokens 등)

    Returns:
        저장된 X-Report 정보 (id, store_id, snapshot_id, prompt_id)

    Raises:
        Exception: DB 트랜잭션 실패 시 rollback 후 예외 발생
    """
    async with get_async_session() as session:
        async with session.begin():
            # ──── 단일 트랜잭션 시작 ────
            job_repo = JobRepo(session)
            store_repo = StoreRepo(session)
            snapshot_repo = SnapshotRepo(session)
            xreport_repo = XReportRepo(session)
            strategy_repo = StrategyRepo(session)

            # 1. Store 조회
            store = await store_repo.get_by_source_id(store_source_id)
            if not store:
                raise ValueError(f"Store not found: store_source_id={store_source_id}")

            # 2. Snapshot 조회/생성
            snapshot = await snapshot_repo.get_by_version(snapshot_version)
            if not snapshot:
                snapshot = await snapshot_repo.create(snapshot_version)
                await session.flush()

            # 3. XReport 생성
            xreport = await xreport_repo.create_completed(
                store_id=store.id,
                snapshot_id=snapshot.id,
                prompt_id=prompt_id,
                content_text=content_text,
                config_json={"analysis_log": analysis_log},
                model_meta=model_meta,  # GPT 응답 메타데이터
            )

            # 4. Strategies 9개 생성
            await strategy_repo.bulk_create(
                x_report_id=xreport.id,
                strategies=strategies,
            )

            # 5. Job 상태 → COMPLETED
            await job_repo.update_status(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                result_id=xreport.id,
                progress=100,
            )

            # ──── 단일 트랜잭션 커밋 (자동) ────
            print(f"[TX2-COMMIT] XReport {xreport.id} + 9 Strategies + Job COMPLETED")

            return {
                "id": xreport.id,
                "store_id": xreport.store_id,
                "snapshot_id": xreport.snapshot_id,
                "prompt_id": xreport.prompt_id,
                "status": xreport.status,
            }


async def _update_job_failed_async(job_id: int, error_message: str) -> None:
    """
    [Transaction 3] Job 상태를 FAILED로 업데이트 (별도 독립 트랜잭션)

    Args:
        job_id: Job ID
        error_message: 구조화된 에러 메시지 (format_error_message로 생성)

    Note:
        트랜잭션 rollback 이후 별도 세션으로 실행됩니다.
        이 함수는 예외를 발생시키지 않습니다 (실패해도 조용히 처리).
    """
    try:
        async with get_async_session() as session:
            async with session.begin():
                job_repo = JobRepo(session)
                await job_repo.update_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error_message=error_message,
                )
        print(f"[TX3-COMMIT] Job {job_id} status → FAILED")
        print(f"[ERROR] Error message: {error_message[:100]}...")
    except Exception as e:
        print(f"[CRITICAL] Failed to update Job {job_id} to FAILED: {e}")
        print(f"[CRITICAL] Original error was: {error_message[:200]}...")
        # Don't raise - 실패 기록 자체가 실패해도 원래 예외를 우선


class XReportGenerationTask(Task):
    """X-Report 생성 Task (상태 추적)"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Task 실패 시 Job 상태 업데이트"""
        job_id = kwargs.get("job_id")
        if job_id:
            # Job 상태를 FAILED로 업데이트하는 로직 추가 가능
            print(f"[ERROR] Task failed for job_id={job_id}: {exc}")


@celery_app.task(
    bind=True,
    base=XReportGenerationTask,
    name="tasks.generate_x_report",
    autoretry_for=(
        openai.APIError,           # OpenAI API 오류 재시도
        requests.Timeout,           # Timeout 오류 재시도
        requests.HTTPError,         # HTTP 5xx 오류 재시도
    ),
    max_retries=3,                  # 최대 3회 재시도
    retry_backoff=True,             # 지수 백오프 (1분 → 2분 → 4분)
    retry_backoff_max=600,          # 최대 10분
    default_retry_delay=60,         # 기본 재시도 간격 60초
)
def generate_x_report(
    self,
    job_id: int,
    store_id: str,
    store_source_id: str,
    store_name: str,
    snapshot_version: str,
    prompt_id: str,
) -> Dict[str, Any]:
    """
    X-Report 생성 Task

    Args:
        job_id: Job ID
        store_id: Store UUID
        store_source_id: 매장 소스 ID
        store_name: 매장 이름
        snapshot_version: 스냅샷 버전
        prompt_id: 프롬프트 ID

    Returns:
        생성된 X-Report 정보

    Raises:
        ValueError: 전략 파싱 실패 (재시도 안 함)
        openai.APIError: OpenAI API 오류 (재시도 후 실패)
        requests.Timeout: 백엔드 API Timeout (재시도 후 실패)
        requests.HTTPError: 백엔드 API 오류 (재시도 후 실패)

    Note:
        - ValueError는 autoretry_for에 포함되지 않아 즉시 실패 처리됩니다.
        - 재시도 가능 예외도 max_retries 초과 시 최종 실패 처리됩니다.
        - 모든 실패는 Job.status를 FAILED로 업데이트하고 error_message를 기록합니다.
    """
    print(f"[START] Generating X-Report for store={store_name}, job_id={job_id}")

    # 변수 초기화 (try 밖에서 선언하여 except 블록에서 접근 가능)
    report_text = None

    try:
        # ──────────────────────────────────────────────────────────
        # [TX1] Job 상태 → PROCESSING (독립 트랜잭션)
        # ──────────────────────────────────────────────────────────
        asyncio.run(_update_job_processing_async(job_id))

        # OpenAI API Key 확인
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in settings")

        client = OpenAI(api_key=api_key)

        # ──────────────────────────────────────────────────────────
        # [외부 API 호출] GPT 분석 및 Report 생성 (트랜잭션 외부)
        # ──────────────────────────────────────────────────────────

        # Step 1: 매장 데이터 조회 (백엔드 API - HTTP)
        print("[1/4] Fetching store data from backend...")
        store_data = fetch_store_data(store_id, snapshot_version)

        # Step 2: GPT 분석 (Temperature 0.1)
        print("[2/4] GPT analysis...")
        analysis_result = call_gpt_analysis(client, store_data, store_name)

        # Step 3: X-Report 생성 (Temperature 0.7, JSON-only)
        print("[3/4] GPT X-Report generation (JSON mode)...")
        report_text, model_meta = call_gpt_report(client, store_data, store_name, analysis_result)
        print(f"[INFO] GPT response: model={model_meta.get('model')}, tokens={model_meta.get('total_tokens', 'N/A')}")

        # Step 4: 9개 전략 추출 (JSON-first → 텍스트 fallback)
        print("[4/4] Extracting strategies (JSON-first parser)...")
        strategies = extract_solutions(report_text)

        # ──────────────────────────────────────────────────────────
        # [TX2] XReport + Strategies + Job COMPLETED (단일 원자적 트랜잭션)
        # ──────────────────────────────────────────────────────────
        print("[5/5] Saving to database (atomic transaction)...")
        result = asyncio.run(_save_report_async(
            job_id=job_id,
            store_source_id=store_source_id,
            snapshot_version=snapshot_version,
            prompt_id=prompt_id,
            content_text=report_text,
            strategies=strategies,
            analysis_log=analysis_result,
            model_meta=model_meta,  # GPT 응답 메타데이터
        ))

        print(f"[SUCCESS] X-Report created: report_id={result.get('id')}")
        return result

    except ValueError as e:
        # ──────────────────────────────────────────────────────────
        # [A] ValueError: 전략 파싱 오류 - 재시도 안 함
        # → TX2 rollback (자동) + TX3 실행 (Job FAILED 기록)
        # ──────────────────────────────────────────────────────────
        error_msg = format_error_message(
            error_code="STRATEGY_PARSE_COUNT_MISMATCH",
            message=str(e),
            context=report_text[:500] if report_text else "No report text generated"
        )
        asyncio.run(_update_job_failed_async(job_id, error_msg))
        print(f"[FAILED] ValueError: {e}")
        raise  # Celery에 실패 전파 (재시도 안 함)

    except requests.Timeout as e:
        # ──────────────────────────────────────────────────────────
        # [B-1] Timeout: 재시도 대상, 최종 실패 시 처리
        # → TX2 rollback (자동) + TX3 실행 (Job FAILED 기록)
        # ──────────────────────────────────────────────────────────
        error_msg = format_error_message(
            error_code="BACKEND_TIMEOUT",
            message=f"Backend API timeout: {str(e)}",
            context=f"URL: {e.request.url if hasattr(e, 'request') else 'N/A'}"
        )
        asyncio.run(_update_job_failed_async(job_id, error_msg))
        print(f"[FAILED] Timeout after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except requests.HTTPError as e:
        # ──────────────────────────────────────────────────────────
        # [B-2] HTTPError: 재시도 대상, 최종 실패 시 처리
        # → TX2 rollback (자동) + TX3 실행 (Job FAILED 기록)
        # ──────────────────────────────────────────────────────────
        error_msg = format_error_message(
            error_code="BACKEND_API_ERROR",
            message=f"Backend API error: {e.response.status_code if e.response else 'N/A'} - {str(e)}",
            context=f"URL: {e.request.url if hasattr(e, 'request') else 'N/A'}"
        )
        asyncio.run(_update_job_failed_async(job_id, error_msg))
        print(f"[FAILED] HTTPError after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except openai.APIError as e:
        # ──────────────────────────────────────────────────────────
        # [B-3] OpenAI APIError: 재시도 대상, 최종 실패 시 처리
        # → TX2 rollback (자동) + TX3 실행 (Job FAILED 기록)
        # ──────────────────────────────────────────────────────────
        error_msg = format_error_message(
            error_code="LLM_API_ERROR",
            message=f"OpenAI API error: {str(e)}",
            context=f"Type: {type(e).__name__}"
        )
        asyncio.run(_update_job_failed_async(job_id, error_msg))
        print(f"[FAILED] OpenAI APIError after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except Exception as e:
        # ──────────────────────────────────────────────────────────
        # [C] 기타 모든 예외: 재시도 안 함
        # → TX2 rollback (자동) + TX3 실행 (Job FAILED 기록)
        # ──────────────────────────────────────────────────────────
        error_msg = format_error_message(
            error_code="UNKNOWN_ERROR",
            message=f"Unexpected error: {type(e).__name__} - {str(e)}",
            context=traceback.format_exc()[:500]
        )
        asyncio.run(_update_job_failed_async(job_id, error_msg))
        print(f"[FAILED] Unexpected error: {e}")
        print(traceback.format_exc())
        raise  # Celery에 실패 전파 (재시도 안 함)


def fetch_store_data(store_id: str, snapshot_version: str) -> Dict[str, Any]:
    """백엔드 API에서 매장 데이터 조회"""
    url = f"http://127.0.0.1:8000/stores/{store_id}/documents"
    params = {}  # snapshot_version 쿼리 파라미터는 현재 엔드포인트에서 지원하지 않음

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch store data: {e}")
        # Fallback to mock data if API call fails
        return {
            "store_name": "테스트 매장",
            "review_metrics": {"overall_sentiment": {"score": 0.9}},
            "population_data": "인구 데이터",
            "sales_metrics": "매출 데이터",
            "industry_context": {"phase": "성장"},
            "critical_feedback": ["피드백 1"],
            "avg_price": "15000원",
        }


def call_gpt_analysis(client: OpenAI, store_data: Dict, store_name: str) -> str:
    """GPT 분석 호출 (Temperature 0.1)"""

    # 프롬프트 구성 (prompts.py와 동일)
    system_prompt = """당신은 대한민국 최고의 'F&B 데이터 사이언티스트'입니다.
당신의 임무는 RAW 데이터를 분석하여 매장의 치명적인 약점과 경쟁 우위를 발굴하는 것입니다."""

    user_prompt = f"""제공된 5대 핵심 데이터를 종합 분석하여 '최우선 분석 결과'를 도출하세요.

[통합 분석 데이터]
1. 매장 기본/리뷰: {store_name} | {json.dumps(store_data.get('review_metrics', {}), ensure_ascii=False)}
2. 인구 통계: {store_data.get('population_data', '정보 부족')}
3. 매출 실적: {store_data.get('sales_metrics', '정보 부족')}
4. 업종/상권: {json.dumps(store_data.get('industry_context', {}), ensure_ascii=False)}
5. 취약 의견: {store_data.get('critical_feedback', '정보 부족')}

반드시 '### 최종 분석 보고' 헤더 다음 상세 내용을 작성하세요."""

    response = client.chat.completions.create(
        model="gpt-5.2",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1
    )

    return response.choices[0].message.content


def call_gpt_report(client: OpenAI, store_data: Dict, store_name: str, analysis_result: str):
    """
    GPT X-Report 생성 (Temperature 0.7) - JSON-only 출력

    Returns:
        Tuple[str, Dict[str, Any]]: (JSON 문자열, model_meta)
        - JSON 문자열: {"categories": [{"title": "...", "solutions": ["...", "...", "..."]}, ...]}
        - model_meta: {"model": "gpt-5.2", "request_id": "...", "prompt_tokens": 123, "completion_tokens": 456, "total_tokens": 579}
    """

    system_prompt = "당신은 외식공인의 성공을 돕는 데이터 기반 비즈니스 전략가 'X-report'입니다."

    user_prompt = f"""제공된 [분석 데이터]를 바탕으로, 시뮬레이션 전 단계인 'X-report'를 작성하세요.
목적: 사장님께 매장의 현황을 알리고, 이후 시뮬레이션에서 테스트해 볼 수 있는 구체적인 솔루션 리스트를 제공하는 것입니다.

[입력 데이터 정보]
- 매장명: {store_name}
- 상권 및 매출/리뷰 데이터: {analysis_result}

[출력 형식 - 매우 중요]
반드시 아래 JSON 스키마에 정확히 맞춰 출력하세요:

{{
  "categories": [
    {{"title": "전략 카테고리 제목 1", "solutions": ["솔루션 1-1 상세 설명", "솔루션 1-2 상세 설명", "솔루션 1-3 상세 설명"]}},
    {{"title": "전략 카테고리 제목 2", "solutions": ["솔루션 2-1 상세 설명", "솔루션 2-2 상세 설명", "솔루션 2-3 상세 설명"]}},
    {{"title": "전략 카테고리 제목 3", "solutions": ["솔루션 3-1 상세 설명", "솔루션 3-2 상세 설명", "솔루션 3-3 상세 설명"]}}
  ]
}}

[엄격한 출력 규칙]
- Do not include any preamble, thoughts, reasoning, explanations, or markdown code blocks (including ```json).
- Output ONLY the raw JSON string that matches the schema.
- 정확히 3개의 카테고리를 생성하세요.
- 각 카테고리는 정확히 3개의 솔루션을 포함해야 합니다 (총 9개).
- 각 솔루션은 구체적이고 실행 가능한 내용이어야 합니다 (최소 50자, 최대 500자).
- JSON 외 다른 텍스트를 출력하지 마세요."""

    response = client.chat.completions.create(
        model="gpt-5.2",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        response_format={"type": "json_object"}  # JSON 모드 강제
    )

    # model_meta 추출
    model_meta = {
        "model": response.model,
        "request_id": response.id,
        "worker": "celery",
    }

    # usage 정보가 있으면 추가
    if response.usage:
        model_meta["prompt_tokens"] = response.usage.prompt_tokens
        model_meta["completion_tokens"] = response.usage.completion_tokens
        model_meta["total_tokens"] = response.usage.total_tokens

    return response.choices[0].message.content, model_meta


def parse_strategies_json(text: str) -> List[Dict[str, Any]]:
    """
    JSON 형식의 GPT 출력을 파싱하여 9개 전략 추출 (JSON-first 파서)

    Args:
        text: GPT 응답 문자열 (JSON 형식 예상)

    Returns:
        9개 솔루션 리스트 (Strategy 모델과 호환되는 dict 형식)

    Raises:
        ValueError: JSON 파싱 실패 또는 스키마 검증 실패

    Note:
        한국어 인코딩 주의:
        - json.loads(text)는 자동으로 UTF-8 디코딩됨 (문제 없음)
        - 디버깅/로그 출력 시 json.dumps() 사용할 경우:
          반드시 ensure_ascii=False 옵션 사용 (한글 깨짐 방지)
          예: json.dumps(data, ensure_ascii=False)

    Examples:
        # 정상 케이스
        >>> json_text = '''
        ... {
        ...   "categories": [
        ...     {"title": "공간 경험 개선", "solutions": ["솔루션 1", "솔루션 2", "솔루션 3"]},
        ...     {"title": "제품 매력 강화", "solutions": ["솔루션 4", "솔루션 5", "솔루션 6"]},
        ...     {"title": "서비스 향상", "solutions": ["솔루션 7", "솔루션 8", "솔루션 9"]}
        ...   ]
        ... }
        ... '''
        >>> strategies = parse_strategies_json(json_text)
        >>> len(strategies)
        9

        # 실패 케이스 (잘못된 솔루션 수)
        >>> bad_json = '{"categories": [{"title": "카테고리", "solutions": ["솔루션1", "솔루션2"]}]}'
        >>> parse_strategies_json(bad_json)
        Traceback (most recent call last):
        ...
        ValueError: Expected 3 categories, got 1
    """
    import re

    # 메트릭 매핑 (카테고리 제목에서 metric 추론)
    METRIC_MAPPING = {
        "공간 경험": "insta", "제품 매력": "taste", "전반 평판": "overall",
        "재방문 록인": "service", "브랜드 인지": "insta", "맛": "taste",
        "가격": "price_value", "가성비": "price_value", "서비스": "service",
        "청결": "service", "분위기": "insta", "인테리어": "insta"
    }

    # ──────────────────────────────────────────────────────────
    # 1. JSON 파싱 (1차 시도: 전체 텍스트)
    # ──────────────────────────────────────────────────────────
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e1:
        # ──────────────────────────────────────────────────────────
        # 2. JSON 파싱 (2차 시도: {} 추출)
        # ──────────────────────────────────────────────────────────
        print(f"[WARNING] JSON parsing failed (1st attempt): {e1}")
        print(f"[INFO] Trying to extract JSON from text (2nd attempt)...")

        # 첫 '{' 부터 마지막 '}' 까지 추출
        first_brace = text.find('{')
        last_brace = text.rfind('}')

        if first_brace == -1 or last_brace == -1 or first_brace >= last_brace:
            raise ValueError(
                f"JSON parsing failed: No valid JSON object found. "
                f"Text preview: {text[:200]}..."
            )

        json_text = text[first_brace:last_brace + 1]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e2:
            raise ValueError(
                f"JSON parsing failed (2nd attempt): {e2}. "
                f"Extracted text: {json_text[:200]}..."
            )

    # ──────────────────────────────────────────────────────────
    # 3. 스키마 검증
    # ──────────────────────────────────────────────────────────
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    if "categories" not in data:
        raise ValueError(f"Missing 'categories' key in JSON. Keys: {list(data.keys())}")

    categories = data["categories"]

    if not isinstance(categories, list):
        raise ValueError(f"'categories' must be a list, got {type(categories).__name__}")

    if len(categories) != 3:
        raise ValueError(f"Expected 3 categories, got {len(categories)}")

    # ──────────────────────────────────────────────────────────
    # 4. 각 카테고리 검증 및 솔루션 추출
    # ──────────────────────────────────────────────────────────
    strategies = []

    for cat_idx, category in enumerate(categories, start=1):
        if not isinstance(category, dict):
            raise ValueError(f"Category {cat_idx} must be a dict, got {type(category).__name__}")

        if "title" not in category or "solutions" not in category:
            raise ValueError(
                f"Category {cat_idx} missing required keys. "
                f"Expected: ['title', 'solutions'], Got: {list(category.keys())}"
            )

        cat_title = category["title"]
        cat_solutions = category["solutions"]

        if not isinstance(cat_title, str):
            raise ValueError(f"Category {cat_idx} title must be string")

        if not isinstance(cat_solutions, list):
            raise ValueError(f"Category {cat_idx} solutions must be list")

        if len(cat_solutions) != 3:
            raise ValueError(
                f"Category {cat_idx} ('{cat_title}') must have 3 solutions, "
                f"got {len(cat_solutions)}"
            )

        # 메트릭 추론
        base_metric = "overall"
        for keyword, metric in METRIC_MAPPING.items():
            if keyword in cat_title:
                base_metric = metric
                break

        # 각 솔루션 처리
        for sol_idx, solution_text in enumerate(cat_solutions):
            if not isinstance(solution_text, str):
                raise ValueError(
                    f"Category {cat_idx}, Solution {sol_idx + 1} must be string, "
                    f"got {type(solution_text).__name__}"
                )

            # 정규화: strip, 공백/개행 정리
            normalized = _normalize_solution_text(solution_text)

            # 빈 문자열/공백-only 검증
            if len(normalized) < 10:
                raise ValueError(
                    f"Category {cat_idx}, Solution {sol_idx + 1} is too short "
                    f"(min 10 chars): '{normalized}'"
                )

            # 제목 추출 (첫 문장 또는 첫 50자)
            title = normalized.split('.')[0].strip()
            if len(title) > 200:
                title = title[:200]
            if len(title) < 5:  # 제목이 너무 짧으면 전체 텍스트 사용
                title = normalized[:200]

            # 메트릭 재정의 (솔루션 텍스트에서도 추론)
            metric = base_metric
            for keyword, target_metric in METRIC_MAPPING.items():
                if keyword in normalized:
                    metric = target_metric
                    break

            strategies.append({
                "solution_group": cat_idx,
                "action_idx": sol_idx,
                "title": title,
                "description": normalized[:500],
                "config": {"metric": metric},
                "impact_mapping": {"impact": 0.15}
            })

    # ──────────────────────────────────────────────────────────
    # 5. 최종 검증: 총 9개
    # ──────────────────────────────────────────────────────────
    if len(strategies) != 9:
        raise ValueError(f"Expected 9 strategies, got {len(strategies)}")

    print(f"[SUCCESS] JSON parsing extracted {len(strategies)} strategies")
    return strategies


def extract_solutions(report_text: str) -> List[Dict[str, Any]]:
    """
    Report에서 정확히 9개 전략 추출 (JSON-first → 텍스트 fallback)

    파싱 전략:
    1. JSON-first: parse_strategies_json() 시도 (우선순위)
    2. Fallback-1: 카테고리 기준 텍스트 파싱
    3. Fallback-2: 전체 텍스트에서 솔루션 추출

    Args:
        report_text: GPT 생성 X-Report 텍스트 (JSON 또는 마크다운)

    Returns:
        9개 솔루션 리스트

    Raises:
        ValueError: 9개 솔루션을 추출하지 못한 경우
    """
    import re

    # 메트릭 매핑
    METRIC_MAPPING = {
        "공간 경험": "insta", "제품 매력": "taste", "전반 평판": "overall",
        "재방문 록인": "service", "브랜드 인지": "insta", "맛": "taste",
        "가격": "price_value", "가성비": "price_value", "서비스": "service",
        "청결": "service", "분위기": "insta", "인테리어": "insta"
    }

    # ──────────────────────────────────────────────────────────
    # 1순위: JSON 파서
    # ──────────────────────────────────────────────────────────
    try:
        strategies = parse_strategies_json(report_text)
        if len(strategies) == 9:
            return strategies
        print(f"[WARNING] JSON parsing returned {len(strategies)} solutions, trying text fallback...")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        # JSON 파싱/스키마 검증 실패만 catch (좁게 catch)
        print(f"[WARNING] JSON parsing failed: {e}")
        # GPT 출력 미리보기 (최대 300자)
        preview = report_text[:300].replace('\n', ' ')
        print(f"[DEBUG] GPT output preview (300 chars): {preview}...")
        print(f"[INFO] Falling back to text parsing...")

    # ──────────────────────────────────────────────────────────
    # 2순위: 카테고리 기준 텍스트 파싱 (기존 로직)
    # ──────────────────────────────────────────────────────────
    try:
        solutions = _extract_by_categories(report_text, METRIC_MAPPING)
        if len(solutions) == 9:
            return solutions
        print(f"[WARNING] Category parsing extracted {len(solutions)} solutions, trying final fallback...")
    except Exception as e:
        print(f"[WARNING] Category parsing failed: {e}, trying final fallback...")

    # ──────────────────────────────────────────────────────────
    # 3순위: 전체 텍스트 스캔 (최종 fallback)
    # ──────────────────────────────────────────────────────────
    try:
        solutions = _extract_fallback(report_text, METRIC_MAPPING)
        if len(solutions) >= 9:
            return solutions[:9]
    except Exception as e:
        print(f"[ERROR] Fallback parsing also failed: {e}")

    # ──────────────────────────────────────────────────────────
    # 모든 파서 실패 - 예외 발생
    # ──────────────────────────────────────────────────────────
    raise ValueError(
        f"Expected 9 solutions, got {len(solutions) if 'solutions' in locals() else 0}. "
        f"All parsers failed. Please check GPT output format."
    )


def _extract_by_categories(report_text: str, metric_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    카테고리 기준 파싱: '전략 카테고리' 헤더로 3개 블록 분리 → 각 블록에서 3개 솔루션 추출
    """
    import re

    solutions = []

    # 카테고리 블록 찾기 (유연한 패턴)
    # "전략 카테고리 1: 제목" 또는 "**전략 카테고리 1: 제목**" 등
    category_pattern = r"(?:^|\n)\s*(?:#{1,3}\s*)?(?:\*{0,2})전략\s*카테고리\s*(\d+)\s*[:：-]\s*(.+?)(?:\*{0,2})\s*$"
    matches = list(re.finditer(category_pattern, report_text, re.MULTILINE | re.IGNORECASE))

    if len(matches) < 3:
        raise ValueError(f"Found only {len(matches)} categories (expected 3)")

    # 각 카테고리 블록 추출
    for i, match in enumerate(matches[:3]):
        category_num = int(match.group(1))
        category_title = match.group(2).strip().strip('*').strip()

        # 블록 범위: 현재 카테고리 시작 ~ 다음 카테고리 시작 (또는 문서 끝)
        block_start = match.end()
        block_end = matches[i+1].start() if i+1 < len(matches) else len(report_text)
        block_content = report_text[block_start:block_end]

        # 메트릭 결정
        base_metric = "overall"
        for keyword, target in metric_mapping.items():
            if keyword in category_title:
                base_metric = target
                break

        # 블록에서 솔루션 추출
        block_solutions = _extract_solutions_from_block(
            block_content,
            category_num,
            base_metric,
            metric_mapping
        )

        solutions.extend(block_solutions)

    return solutions


def _extract_solutions_from_block(
    block_content: str,
    category_num: int,
    base_metric: str,
    metric_mapping: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    단일 카테고리 블록에서 솔루션 추출 (라인 스캐너 방식)
    """
    import re

    solutions = []

    # 솔루션 시작 패턴 (유연함)
    # "- 솔루션 1:", "• **솔루션 2:**", "1. Solution 3:" 등
    solution_start_pattern = r"^[\s\-•\*]*(?:\d+\.|\-|•)\s*(?:\*{0,2})(?:솔루션|Solution)\s*(\d+)\s*[:：]\s*(?:\*{0,2})\s*(.*)$"

    lines = block_content.split('\n')
    current_solution = None
    current_solution_num = None

    for line in lines:
        line_stripped = line.strip()

        # 솔루션 시작 감지
        match = re.match(solution_start_pattern, line_stripped, re.IGNORECASE)
        if match:
            # 이전 솔루션 저장
            if current_solution is not None:
                _save_solution(
                    solutions,
                    current_solution_num,
                    current_solution,
                    category_num,
                    base_metric,
                    metric_mapping
                )

            # 새 솔루션 시작
            current_solution_num = int(match.group(1))
            current_solution = match.group(2).strip()
        elif current_solution is not None:
            # 솔루션 설명 계속 (여러 줄 병합)
            if line_stripped:  # 빈 줄 제외
                current_solution += " " + line_stripped

    # 마지막 솔루션 저장
    if current_solution is not None:
        _save_solution(
            solutions,
            current_solution_num,
            current_solution,
            category_num,
            base_metric,
            metric_mapping
        )

    return solutions


def _extract_fallback(report_text: str, metric_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Fallback: 전체 텍스트에서 솔루션만 추출 (카테고리 구분 없이)
    """
    import re

    solutions = []

    # 솔루션 패턴 (더 유연함)
    pattern = r"(?:^|\n)[\s\-•\*]*(?:\d+\.|\-|•)\s*(?:\*{0,2})(?:솔루션|Solution)\s*(\d+)\s*[:：]\s*(?:\*{0,2})\s*(.+?)(?=\n[\s\-•\*]*(?:\d+\.|\-|•)\s*(?:\*{0,2})(?:솔루션|Solution)|$)"

    matches = re.findall(pattern, report_text, re.MULTILINE | re.DOTALL | re.IGNORECASE)

    for sol_num, sol_text in matches:
        sol_text = _normalize_solution_text(sol_text)

        if len(sol_text) < 10:  # 너무 짧은 솔루션 제외
            continue

        # 첫 문장을 제목으로
        title = sol_text.split('.')[0].strip()
        if len(title) > 200:
            title = title[:200]

        # 메트릭 추론
        metric = "overall"
        for keyword, target in metric_mapping.items():
            if keyword in sol_text:
                metric = target
                break

        # solution_group은 순서 기반 (1-3: 그룹1, 4-6: 그룹2, 7-9: 그룹3)
        solution_group = ((len(solutions)) // 3) + 1

        solutions.append({
            "solution_group": solution_group,
            "action_idx": int(sol_num),
            "title": title,
            "description": sol_text[:500],
            "config": {"metric": metric},
            "impact_mapping": {"impact": 0.15}
        })

    return solutions


def _save_solution(
    solutions: List[Dict[str, Any]],
    sol_num: int,
    sol_text: str,
    category_num: int,
    base_metric: str,
    metric_mapping: Dict[str, str]
):
    """솔루션 정규화 후 리스트에 추가"""
    sol_text = _normalize_solution_text(sol_text)

    if len(sol_text) < 10:  # 너무 짧은 솔루션 제외
        return

    # 첫 문장을 제목으로
    title = sol_text.split('.')[0].strip()
    if len(title) > 200:
        title = title[:200]

    # 메트릭 재정의
    metric = base_metric
    for keyword, target in metric_mapping.items():
        if keyword in sol_text:
            metric = target
            break

    solutions.append({
        "solution_group": category_num,
        "action_idx": sol_num,
        "title": title,
        "description": sol_text[:500],
        "config": {"metric": metric},
        "impact_mapping": {"impact": 0.15}
    })


def _normalize_solution_text(text: str) -> str:
    """
    솔루션 텍스트 정규화
    - 불필요한 prefix 제거
    - 공백/개행 정리
    - Bold 마크다운 제거
    """
    import re

    # Bold 마크다운 제거
    text = re.sub(r'\*{2,}', '', text)

    # 불필요한 prefix 제거
    # "솔루션 1:", "Solution 2:", "1.", "•", "-" 등
    text = re.sub(r'^(?:솔루션|Solution)\s*\d+\s*[:：]\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^\d+\.\s*', '', text)
    text = re.sub(r'^[\-•※]\s*', '', text)

    # 연속된 공백/개행 정리
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()

    return text


def update_job_status(job_id: int, status: str) -> None:
    """
    [DEPRECATED] Job 상태 업데이트 (HTTP 방식 - 레거시)

    Args:
        job_id: Job ID
        status: 업데이트할 상태 (processing, completed, failed)

    Note:
        이 함수는 더 이상 사용되지 않습니다.
        대신 _update_job_processing_async 또는 _update_job_failed_async를 사용하세요.
    """
    url = f"http://127.0.0.1:8000/jobs/{job_id}/status"
    payload = {"status": status}
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.WORKER_API_KEY,
    }

    try:
        # Timeout: (connect=2초, read=5초)
        response = requests.patch(url, json=payload, headers=headers, timeout=(2, 5))
        response.raise_for_status()
        print(f"[INFO] Job {job_id} status updated to {status}")
    except requests.exceptions.RequestException as e:
        print(f"[WARNING] Failed to update job status: {e}")
        # Don't raise - this is not critical


def update_job_failed(job_id: int, error_message: str) -> None:
    """
    [DEPRECATED] Job 상태를 FAILED로 업데이트 (HTTP 방식 - 레거시)

    Args:
        job_id: Job ID
        error_message: 구조화된 에러 메시지 (format_error_message로 생성)

    Note:
        이 함수는 더 이상 사용되지 않습니다.
        대신 _update_job_failed_async를 사용하세요.
    """
    url = f"http://127.0.0.1:8000/jobs/{job_id}/status"
    payload = {
        "status": "failed",
        "error_message": error_message
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.WORKER_API_KEY,
    }

    try:
        # Timeout: (connect=2초, read=5초)
        response = requests.patch(url, json=payload, headers=headers, timeout=(2, 5))
        response.raise_for_status()
        print(f"[ERROR] Job {job_id} marked as FAILED: {error_message[:100]}...")
    except requests.exceptions.RequestException as e:
        print(f"[CRITICAL] Failed to update job {job_id} to FAILED status: {e}")
        print(f"[CRITICAL] Original error was: {error_message[:200]}...")
        # Don't raise - we already know the task failed


def save_to_backend(
    job_id: int,
    store_source_id: str,
    snapshot_version: str,
    prompt_id: str,
    content_text: str,
    strategies: List[Dict],
    analysis_log: str,
) -> Dict[str, Any]:
    """
    [DEPRECATED] 백엔드 API로 X-Report 저장 (HTTP 방식 - 레거시)

    Args:
        job_id: Job ID
        store_source_id: 매장 소스 ID
        snapshot_version: 스냅샷 버전
        prompt_id: 프롬프트 ID
        content_text: X-Report 본문
        strategies: 9개 전략 리스트
        analysis_log: GPT 분석 로그

    Returns:
        저장된 X-Report 정보

    Raises:
        requests.Timeout: 백엔드 API 타임아웃
        requests.HTTPError: 백엔드 API HTTP 오류

    Note:
        이 함수는 더 이상 사용되지 않습니다.
        대신 _save_report_async를 사용하세요 (DB 직접 접근 방식).
    """
    url = "http://127.0.0.1:8000/x-reports/save"

    payload = {
        "job_id": job_id,
        "store_source_id": store_source_id,
        "snapshot_version": snapshot_version,
        "prompt_id": prompt_id,
        "content_text": content_text,
        "config_json": {"analysis_log": analysis_log},
        "model_meta": {"model": "gpt-5.2", "worker": "celery"},
        "strategies": strategies,
    }

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.WORKER_API_KEY,
    }

    try:
        # Timeout: (connect timeout, read timeout)
        # connect: 3초 - 서버 연결 시도
        # read: 30초 - 응답 대기 (DB 트랜잭션 고려)
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=(3.05, 30)
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to save to backend: {e}")
        raise  # Celery task에서 재시도 또는 실패 처리
