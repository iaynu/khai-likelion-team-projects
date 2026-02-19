"""
X-Report 생성 Celery Task
GPT 기반 X-Report 생성 후 백엔드 API로 저장
"""
import os
import json
import requests
from typing import Dict, Any, List
from celery import Task
from openai import OpenAI

from celery_app import celery_app
from app.core.config import settings


class XReportGenerationTask(Task):
    """X-Report 생성 Task (상태 추적)"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Task 실패 시 Job 상태 업데이트"""
        job_id = kwargs.get("job_id")
        if job_id:
            # Job 상태를 FAILED로 업데이트하는 로직 추가 가능
            print(f"[ERROR] Task failed for job_id={job_id}: {exc}")


@celery_app.task(bind=True, base=XReportGenerationTask, name="tasks.generate_x_report")
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
    """
    print(f"[START] Generating X-Report for store={store_name}, job_id={job_id}")

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
    """Report에서 9개 전략 추출 (3개 카테고리 × 3개 솔루션)"""
    import re

    solutions = []

    # 메트릭 매핑
    mapping = {
        "공간 경험": "insta",
        "제품 매력": "taste",
        "전반 평판": "overall",
        "재방문 록인": "service",
        "브랜드 인지": "insta",
        "맛": "taste",
        "가격": "price_value",
        "가성비": "price_value",
        "서비스": "service",
        "청결": "service",
        "분위기": "insta",
        "인테리어": "insta"
    }

    # 카테고리별 파싱
    categories = re.split(r"### 🔹 전략 카테고리 \d+[:.]\s*\*{0,2}\[?(.*?)\]?\*{0,2}\s*(?:\(.*?\))?\s*\n", report_text)

    for i in range(1, len(categories), 2):
        theme = categories[i].strip().strip('*').strip('[').strip(']').strip()
        content = categories[i+1] if i+1 < len(categories) else ""

        # 기본 메트릭
        base_metric = "overall"
        for keyword, target in mapping.items():
            if keyword in theme:
                base_metric = target
                break

        # 개별 솔루션 추출
        sols = re.findall(r"-\s*\*{0,2}솔루션 [A-Z][:.]\*{0,2}\s*(.*?)(?=\n-\s*\*{0,2}솔루션 [A-Z]|### |## |$)", content, re.DOTALL)
        for idx, sol_text in enumerate(sols, 1):
            sol_name = sol_text.strip().split('\n')[0].strip().strip('*')
            if not sol_name or len(sol_name) < 3:
                continue

            # 솔루션별 메트릭 재정의
            sol_metric = base_metric
            for keyword, target in mapping.items():
                if keyword in sol_name:
                    sol_metric = target
                    break

            # solution_group 계산 (1, 2, 3)
            solution_group = (i // 2) + 1

            solutions.append({
                "solution_group": solution_group,
                "action_idx": idx,
                "title": sol_name[:200],  # 제목 길이 제한
                "description": sol_text[:500],  # 설명 길이 제한
                "config": {"metric": sol_metric},
                "impact_mapping": {"impact": 0.15}
            })

    # 9개가 안 되면 경고
    if len(solutions) < 9:
        print(f"[WARNING] Only {len(solutions)} solutions extracted (expected 9)")

    return solutions[:9]  # 최대 9개


def update_job_status(job_id: int, status: str) -> None:
    """Job 상태 업데이트"""
    url = f"http://127.0.0.1:8000/jobs/{job_id}/status"
    payload = {"status": status}
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": settings.WORKER_API_KEY,
    }

    try:
        response = requests.patch(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        print(f"[INFO] Job {job_id} status updated to {status}")
    except requests.exceptions.RequestException as e:
        print(f"[WARNING] Failed to update job status: {e}")
        # Don't raise - this is not critical


def save_to_backend(
    job_id: int,
    store_source_id: str,
    snapshot_version: str,
    prompt_id: str,
    content_text: str,
    strategies: List[Dict],
    analysis_log: str,
) -> Dict[str, Any]:
    """백엔드 API로 X-Report 저장"""

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
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to save to backend: {e}")
        raise
