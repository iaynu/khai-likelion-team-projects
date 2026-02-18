# Backbone Backend

FastAPI + PostgreSQL 기반 상권 분석 백엔드. 원천 데이터 적재(Ingest) → X-Report 생성 → 전략(Strategy) 구조화까지를 담당합니다.

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| Web | FastAPI, Uvicorn |
| ORM | SQLAlchemy 2.0 (Async) |
| Migration | Alembic |
| DB | PostgreSQL 15+ |
| 컨테이너 | Docker, docker-compose |
| 테스트 | pytest, pytest-asyncio, httpx, aiosqlite |

---

## 프로젝트 구조

```
app/
├── main.py                 # FastAPI app + 라우터 등록
├── core/
│   ├── config.py           # 환경변수 (pydantic-settings)
│   ├── logging.py          # 구조화 로깅
│   └── schemas/            # jsonschema 검증 파일
│       ├── ingest_market_sales_review.json
│       └── ingest_population.json
├── db/                     # engine / session
├── models/                 # SQLAlchemy ORM 모델
├── schemas/                # Pydantic v2 request/response
├── repositories/           # DB 접근 레이어
├── services/               # 비즈로직 오케스트레이션
│   ├── ingest_service.py
│   ├── x_report_service.py
│   └── llm_mock_provider.py
└── api/routers/            # FastAPI 라우터
migrations/                 # Alembic migration
tests/                      # pytest
```

---

## 빠른 시작

### 1. docker-compose 시작

```bash
docker-compose up -d
```

`postgres` 컨테이너가 healthy 상태가 되면 `api` 컨테이너가 자동 시작됩니다.

### 2. DB Migration 실행

컨테이너 내부에서 Alembic 실행:

```bash
docker exec -it $(docker ps -aqf name=api) \
  alembic upgrade head
```

또는 로컬 환경(venv)에서:

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/backbone_db
alembic upgrade head
```

### 3. Ingest → X-Report → 조회

#### 3-1. 데이터 Ingest

```bash
curl -s -X POST http://localhost:8000/admin/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "snapshot_version": "20253_v1",
    "market_sales_review": [
      {
        "id": "망원2동_테스트_양식음식점_20253",
        "market_analysis": { "phase": "성장", "trend_review": "테스트" },
        "raw_data_context": { "metrics": { "store_count": 1, "net_change": 0, "close_rate": 0, "has_sales_data": false } },
        "metadata": { "area": "망원2동", "sector": "양식음식점", "quarter": "20253" },
        "analysis_reports": [
          { "store_id": "1001", "store_name": "테스트카페" }
        ],
        "report_count": 1
      }
    ],
    "population": [
      {
        "id": "11440690_20253_population",
        "population_analysis": { "overall_summary": "거주인구 18000명" },
        "raw_data_context": { "metrics": { "reside_total": 18000, "floating_total": 6000000 } },
        "metadata": { "area_code": "11440690", "quarter": "20253" }
      }
    ]
  }' | jq .
```

#### 3-2. X-Report 생성

```bash
curl -s -X POST http://localhost:8000/x-reports \
  -H "Content-Type: application/json" \
  -d '{
    "store_source_id": "1001",
    "snapshot_version": "20253_v1",
    "prompt_id": "strategy_v1",
    "force_regenerate": false
  }' | jq .
```

#### 3-3. Strategies 조회

```bash
# {REPORT_ID}는 위 응답의 "id" 값
curl -s http://localhost:8000/x-reports/{REPORT_ID}/strategies | jq .
```

#### 3-4. 기타 조회

```bash
# 전체 매장 목록
curl -s http://localhost:8000/stores | jq .

# 특정 매장 문서 조회
curl -s http://localhost:8000/stores/{STORE_UUID}/documents | jq .
```

---

## 테스트 실행

```bash
pip install -r requirements.txt
pytest tests/ -v
```

in-memory SQLite를 사용하여 DB 없이 실행됩니다.

---

## 주요 설계 포인트

| 항목 | 설명 |
|---|---|
| store_source_id | 원천 JSON의 `analysis_reports[].store_id` — business key로 upsert |
| merged_input | `fn_get_merged_input()` Postgres function으로 동적 생성 (물리 저장 없음) |
| X-Report 캐싱 | `(store_id, snapshot_id, prompt_id)` 조합 존재 시 재사용; `force_regenerate=true`로 강제 재생성 |
| 전략 12개 | 3개 솔루션 그룹 × 4개 액션 = 12개, 단일 트랜잭션으로 원자적 저장 |
| Celery | `x_report_service.py` TODO 주석에 도입 지점 표시 |
| jsonschema | `app/core/schemas/*.json` 파일로 관리; ingest 시 자동 검증 |
