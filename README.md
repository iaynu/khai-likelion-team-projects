# Backbone Backend

FastAPI + PostgreSQL + Celery 기반 상권 분석 백엔드. 비동기 Job Queue를 통한 X-Report 생성 시스템.

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| Web | FastAPI, Uvicorn |
| ORM | SQLAlchemy 2.0 (Async) |
| Migration | Alembic |
| DB | PostgreSQL 15+ / SQLite (개발/테스트) |
| Job Queue | Celery, Redis |
| LLM | OpenAI gpt-5.2 |
| 컨테이너 | Docker, docker-compose |
| 테스트 | pytest, pytest-asyncio, httpx, aiosqlite |

---

## 아키텍처

```
┌─────────────────┐
│  FastAPI 서버   │ (포트 8000)
│  POST /jobs     │
└─────────────────┘
        ↓
┌─────────────────┐
│   Redis Broker  │ (포트 6379)
└─────────────────┘
        ↓
┌─────────────────┐
│  Celery Worker  │
│  (GPU 가능)     │
└─────────────────┘
        ↓
┌─────────────────┐
│  OpenAI API     │
│  (gpt-5.2)      │
└─────────────────┘
        ↓
┌─────────────────┐
│  POST /x-reports│
│  /save          │
└─────────────────┘
```

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
│   ├── job.py              # Job 모델 (비동기 작업 관리)
│   ├── store.py
│   ├── x_report.py
│   └── strategy.py
├── schemas/                # Pydantic v2 request/response
│   ├── job.py              # Job 스키마
│   └── ...
├── repositories/           # DB 접근 레이어
│   ├── job_repo.py
│   └── ...
├── services/               # 비즈로직 오케스트레이션
│   ├── ingest_service.py
│   └── llm_mock_provider.py
└── api/routers/            # FastAPI 라우터
    ├── jobs.py             # Job 관리 API
    ├── stores.py
    └── x_reports.py        # X-Report API (Worker 전용 /save 포함)

tasks/                      # Celery Tasks
├── __init__.py
└── x_report_task.py        # X-Report 생성 Task (GPT 통합)

celery_app.py               # Celery 앱 설정
migrations/                 # Alembic migration
tests/                      # pytest
```

---

## 빠른 시작

### 1. 환경 설정

#### 1-1. Redis 설치 (Docker 권장)

```bash
docker run -d -p 6379:6379 --name redis-backbone redis:alpine
```

#### 1-2. 환경 변수 설정 (.env)

```env
# Database
DATABASE_URL=sqlite+aiosqlite:///./backbone.db

# Redis & Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# API Keys
WORKER_API_KEY=your-secret-worker-key
OPENAI_API_KEY=sk-...
```

#### 1-3. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 서버 실행

#### 2-1. FastAPI 서버 (터미널 1)

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

#### 2-2. Celery Worker (터미널 2)

```bash
# Windows
celery -A celery_app worker --loglevel=info --pool=solo

# Linux/Mac
celery -A celery_app worker --loglevel=info
```

#### 2-3. (선택) Flower 모니터링 (터미널 3)

```bash
celery -A celery_app flower --port=5555
```

웹 브라우저: http://localhost:5555

---

## 사용 예시

### 1. 데이터 Ingest

원천 데이터를 데이터베이스에 적재:

```bash
python ingest_data.py
```

### 2. X-Report 생성 (비동기 Job)

#### 2-1. Job 생성

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "x_report",
    "store_source_id": "1839559562",
    "snapshot_version": "v1",
    "prompt_id": "default"
  }'
```

**응답:**
```json
{
  "id": 1,
  "celery_task_id": "abc123...",
  "job_type": "x_report",
  "status": "pending",
  "store_source_id": "1839559562",
  "snapshot_version": "v1",
  "prompt_id": "default",
  "result_id": null,
  "progress": 0
}
```

#### 2-2. Job 상태 확인

```bash
curl http://127.0.0.1:8000/jobs/1
```

**응답:**
```json
{
  "id": 1,
  "status": "completed",  # pending → processing → completed
  "result_id": 5,         # x_report_id
  "progress": 100
}
```

#### 2-3. X-Report 조회

```bash
curl http://127.0.0.1:8000/x-reports/5
```

#### 2-4. 9개 Strategy 조회

```bash
curl http://127.0.0.1:8000/x-reports/5/strategies
```

### 3. 기타 API

```bash
# 매장 목록
curl http://127.0.0.1:8000/stores

# 특정 매장 조회
curl http://127.0.0.1:8000/stores/{store_id}

# 매장의 문서 조회
curl http://127.0.0.1:8000/stores/{store_id}/documents

# Job 목록 (상태별 필터링 가능)
curl http://127.0.0.1:8000/jobs?status=completed

# API 문서
http://127.0.0.1:8000/docs
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
| **비동기 Job Queue** | Celery + Redis로 장시간 실행 작업(GPT 호출) 비동기 처리 |
| **Worker API Key** | Worker가 `/x-reports/save` 호출 시 `X-API-Key` 헤더로 인증 |
| **Job 상태 관리** | pending → processing → completed/failed |
| **gpt-5.2 통합** | 2단계 호출: (1) 분석(temp=0.1) → (2) 전략 생성(temp=0.7) |
| **store_source_id** | 원천 JSON의 `store_id` — business key로 upsert |
| **merged_input** | `fn_get_merged_input()` Postgres function으로 동적 생성 |
| **전략 9개** | 3개 솔루션 그룹 × 3개 액션 = 9개, 단일 트랜잭션 저장 |
| **확장성** | GPU 머신에 Worker 추가 배포로 병렬 처리 확장 가능 |

---

## Troubleshooting

### Redis 연결 오류

```
Error: [Errno 10061] No connection could be made
```

**해결:**
```bash
# Redis 실행 확인
redis-cli ping

# Redis 시작
docker start redis-backbone
```

### Celery Worker 오류

```
Error: No module named 'celery_app'
```

**해결:**
```bash
# 올바른 디렉토리에서 실행
cd c:/Users/user/backend
celery -A celery_app worker --loglevel=info --pool=solo
```

### OpenAI API 오류

```
Error: OPENAI_API_KEY not set in settings
```

**해결:**
`.env` 파일에 API Key 추가

---

## 다음 단계

- [ ] Worker에서 실제 Store 데이터 조회 구현 ✅
- [ ] GPT Prompt 최적화
- [ ] Strategy 추출 Regex 개선 (현재 0개 추출 이슈)
- [ ] Y-Report Task 추가
- [ ] Simulation Task 추가
- [ ] GPU 머신 배포
- [ ] Production 환경 설정 (PostgreSQL, 환경변수 관리)

---

## 라이선스

MIT
