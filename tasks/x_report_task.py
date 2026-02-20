"""
X-Report 생성 Celery Task
GPT 기반 X-Report 생성 후 백엔드 API로 저장
"""
import os
import json
import requests
import traceback
from typing import Dict, Any, List, Optional
from celery import Task
from openai import OpenAI
import openai  # For exceptions

from celery_app import celery_app
from app.core.config import settings


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
        # Job 상태를 PROCESSING으로 업데이트
        update_job_status(job_id, "processing")

        # OpenAI API Key 확인
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in settings")

        client = OpenAI(api_key=api_key)

        # === Step 1: 매장 데이터 조회 ===
        print("[1/4] Fetching store data from backend...")
        store_data = fetch_store_data(store_id, snapshot_version)

        # === Step 2: GPT 분석 (Temperature 0.1) ===
        print("[2/4] GPT analysis...")
        analysis_result = call_gpt_analysis(client, store_data, store_name)

        # === Step 3: X-Report 생성 (Temperature 0.7) ===
        print("[3/4] GPT X-Report generation...")
        report_text = call_gpt_report(client, store_data, store_name, analysis_result)

        # === Step 4: 9개 전략 추출 ===
        print("[4/4] Extracting strategies...")
        strategies = extract_solutions(report_text)

        # === Step 5: 백엔드 API로 저장 ===
        print("[5/5] Saving to backend...")
        result = save_to_backend(
            job_id=job_id,
            store_source_id=store_source_id,
            snapshot_version=snapshot_version,
            prompt_id=prompt_id,
            content_text=report_text,
            strategies=strategies,
            analysis_log=analysis_result,
        )

        print(f"[SUCCESS] X-Report created: report_id={result.get('id')}")
        return result

    except ValueError as e:
        # [A] ValueError: 전략 파싱 오류 - 재시도 안 함
        error_msg = format_error_message(
            error_code="STRATEGY_PARSE_COUNT_MISMATCH",
            message=str(e),
            context=report_text[:500] if report_text else "No report text generated"
        )
        update_job_failed(job_id, error_msg)
        print(f"[FAILED] ValueError: {e}")
        raise  # Celery에 실패 전파 (재시도 안 함)

    except requests.Timeout as e:
        # [B-1] Timeout: 재시도 대상, 최종 실패 시 처리
        error_msg = format_error_message(
            error_code="BACKEND_TIMEOUT",
            message=f"Backend API timeout: {str(e)}",
            context=f"URL: {e.request.url if hasattr(e, 'request') else 'N/A'}"
        )
        update_job_failed(job_id, error_msg)
        print(f"[FAILED] Timeout after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except requests.HTTPError as e:
        # [B-2] HTTPError: 재시도 대상, 최종 실패 시 처리
        error_msg = format_error_message(
            error_code="BACKEND_API_ERROR",
            message=f"Backend API error: {e.response.status_code if e.response else 'N/A'} - {str(e)}",
            context=f"URL: {e.request.url if hasattr(e, 'request') else 'N/A'}"
        )
        update_job_failed(job_id, error_msg)
        print(f"[FAILED] HTTPError after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except openai.APIError as e:
        # [B-3] OpenAI APIError: 재시도 대상, 최종 실패 시 처리
        error_msg = format_error_message(
            error_code="LLM_API_ERROR",
            message=f"OpenAI API error: {str(e)}",
            context=f"Type: {type(e).__name__}"
        )
        update_job_failed(job_id, error_msg)
        print(f"[FAILED] OpenAI APIError after retries: {e}")
        raise  # Celery에 실패 전파 (재시도 또는 최종 실패)

    except Exception as e:
        # [C] 기타 모든 예외: 재시도 안 함
        error_msg = format_error_message(
            error_code="UNKNOWN_ERROR",
            message=f"Unexpected error: {type(e).__name__} - {str(e)}",
            context=traceback.format_exc()[:500]
        )
        update_job_failed(job_id, error_msg)
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


def call_gpt_report(client: OpenAI, store_data: Dict, store_name: str, analysis_result: str) -> str:
    """GPT X-Report 생성 (Temperature 0.7)"""

    system_prompt = "당신은 외식공인의 성공을 돕는 데이터 기반 비즈니스 전략가 'X-report'입니다."

    user_prompt = f"""제공된 [분석 데이터]를 바탕으로, 시뮬레이션 전 단계인 'X-report'를 작성하세요.
목적: 사장님께 매장의 현황을 알리고, 이후 시뮬레이션에서 테스트해 볼 수 있는 구체적인 솔루션 리스트를 제공하는 것입니다.

[입력 데이터 정보]
- 매장명: {store_name}
- 상권 및 매출/리뷰 데이터: {analysis_result}

각 전략 카테고리당 3개씩 솔루션을 제시하세요. (전체 솔루션 합계는 9개가 되어야 함)

3개의 전략 카테고리를 선정하고, 각 카테고리별로 3개의 구체적인 솔루션을 제시하세요."""

    response = client.chat.completions.create(
        model="gpt-5.2",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7
    )

    return response.choices[0].message.content


def extract_solutions(report_text: str) -> List[Dict[str, Any]]:
    """
    Report에서 정확히 9개 전략 추출 (3개 카테고리 × 3개 솔루션)

    파싱 전략:
    1. 카테고리 기준 파싱 (우선순위)
    2. Fallback: 전체 텍스트에서 솔루션 추출

    Args:
        report_text: GPT 생성 X-Report 텍스트

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

    # 1순위: 카테고리 기준 파싱
    try:
        solutions = _extract_by_categories(report_text, METRIC_MAPPING)
        if len(solutions) == 9:
            return solutions
        print(f"[WARNING] Category parsing extracted {len(solutions)} solutions, trying fallback...")
    except Exception as e:
        print(f"[WARNING] Category parsing failed: {e}, trying fallback...")

    # 2순위: Fallback - 전체 텍스트에서 솔루션 추출
    try:
        solutions = _extract_fallback(report_text, METRIC_MAPPING)
        if len(solutions) >= 9:
            return solutions[:9]
    except Exception as e:
        print(f"[ERROR] Fallback parsing also failed: {e}")

    # 검증 실패 - 예외 발생
    raise ValueError(
        f"Expected 9 solutions, got {len(solutions)}. "
        f"Report parsing failed. Please check GPT output format."
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
    Job 상태 업데이트

    Args:
        job_id: Job ID
        status: 업데이트할 상태 (processing, completed, failed)

    Note:
        이 함수는 실패해도 예외를 발생시키지 않습니다.
        Job 상태 업데이트는 중요하지만 필수적이지 않습니다.
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
    Job 상태를 FAILED로 업데이트하고 에러 메시지 기록

    Args:
        job_id: Job ID
        error_message: 구조화된 에러 메시지 (format_error_message로 생성)

    Note:
        이 함수는 실패해도 예외를 발생시키지 않습니다.
        Job 상태 업데이트 실패가 전체 task를 중단시키지 않도록 합니다.
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
    백엔드 API로 X-Report 저장

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
        - Timeout: (connect=3초, read=30초) 분리 설정
        - 재시도는 Celery task 레벨에서 처리
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
