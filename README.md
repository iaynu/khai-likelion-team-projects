# Backbone Backend

FastAPI + PostgreSQL + Celery 기반 소상공인 AI 상권 분석 플랫폼 백엔드.
X-Report(동기), Y-Report / Simulation(비동기 Job Queue)을 통해 매장 전략 처방 → 시뮬레이션 → 비교 분석 전 과정을 지원합니다.

---

## 기술 스택

| 영역 | 기술 |
|---|---|
| Web Framework | FastAPI 0.109+, Uvicorn |
| ORM | SQLAlchemy 2.0 (Async) |
| Migration | Alembic 1.13 |
| DB | PostgreSQL 15+ (운영) / SQLite + aiosqlite (개발·테스트) |
| Job Queue | Celery 5.3, Redis 5.0 |
| Monitoring | Flower 2.0 |
| LLM | OpenAI GPT-4-turbo (2단계 파이프라인) |
| Validation | Pydantic v2.6 |
| 컨테이너 | Docker, docker-compose |
| 테스트 | pytest 7.4, pytest-asyncio 0.23, httpx, aiosqlite |
| Python | 3.9.7 (anaconda3) |

---

## 시스템 아키텍처

```
┌──────────────────────────────────────────────────────┐
│                  React Frontend                       │
│  - 매장 선택 → X-Report → 시뮬레이션 설정 →          │
│    시뮬레이션 맵 → Y-Report                           │
└────────────────────┬─────────────────────────────────┘
                     │ REST API (HTTP/JSON)
                     ↓
┌──────────────────────────────────────────────────────┐
│              FastAPI 서버 (:8000)                     │
│                                                      │
│  [동기]  POST /x-reports → 인라인 GPT 호출 → 즉시 반환│
│  [비동기] POST /simulations  ─┐                      │
│           POST /y-reports   ─┤→ Celery Task 발행     │
│           GET  /jobs/{id}    ←┘ (프론트 폴링)         │
└──────────┬───────────────────────────────────────────┘
           │
┌──────────▼──────────┐
│   Redis Broker       │ (:6379)  — Celery 메시지 브로커
└──────────┬──────────┘
           │
┌──────────▼──────────────────────────────────────────┐
│                 Celery Worker                        │
│  - simulation_task  : 에이전트 기반 시뮬레이션 실행   │
│  - y_report_task    : 전후 비교 분석 + LLM 보고서     │
│  - x_report_task    : (선택적 비동기 경로 지원)        │
│  - DB 직접 접근 (asyncio.run)                        │
└──────────┬──────────────────────────────────────────┘
           │
┌──────────▼──────────┐
│   PostgreSQL (운영)  │  — 모든 리포트·시뮬레이션 결과 영속화
│   SQLite (개발)      │
└──────────────────────┘
```

---

## 프로젝트 구조

```
backend/
├── app/
│   ├── main.py                     # FastAPI 앱 초기화, CORS, 라우터 등록
│   ├── core/
│   │   ├── config.py               # 환경변수 (pydantic-settings)
│   │   ├── logging.py              # 구조화 로깅
│   │   └── schemas/                # JSON Schema 검증 파일
│   ├── db/
│   │   ├── base.py                 # Base declarative
│   │   └── session.py              # async session factory
│   ├── engine/                     # lazy get_engine() / reset_engine()
│   ├── models/                     # SQLAlchemy ORM 모델
│   │   ├── job.py
│   │   ├── store.py
│   │   ├── x_report.py
│   │   ├── strategy.py
│   │   ├── simulation.py
│   │   └── y_report.py
│   ├── schemas/                    # Pydantic v2 스키마
│   │   ├── job.py
│   │   ├── store.py
│   │   ├── x_report.py
│   │   ├── strategy.py
│   │   ├── simulation.py
│   │   └── y_report.py
│   ├── repositories/               # DB 접근 레이어
│   │   ├── job_repo.py
│   │   ├── store_repo.py
│   │   ├── x_report_repo.py
│   │   ├── strategy_repo.py
│   │   ├── simulation_repo.py
│   │   └── y_report_repo.py
│   ├── services/                   # 비즈니스 로직
│   │   ├── x_report_service.py     # X-Report 생성 오케스트레이션
│   │   ├── x_report_view_service.py# X-Report View DTO 빌더
│   │   ├── y_report_service.py     # Y-Report 생성 오케스트레이션
│   │   ├── y_report_view_service.py# Y-Report View DTO 빌더
│   │   ├── simulation_service.py   # 시뮬레이션 실행 로직
│   │   ├── store_service.py        # 매장 조회 및 등급 계산
│   │   ├── store_grade_service.py  # 등급 및 퍼센타일 산출
│   │   ├── llm_gpt_provider.py     # OpenAI GPT-4-turbo 연동
│   │   ├── llm_mock_provider.py    # 테스트용 Mock LLM
│   │   └── ingest_service.py       # 원천 데이터 적재
│   ├── adapters/                   # 외부 서비스 어댑터
│   └── api/routers/
│       ├── admin.py
│       ├── jobs.py                 # Job 생성·조회
│       ├── stores.py               # 매장 목록·검색
│       ├── x_reports.py            # X-Report 생성·조회
│       ├── simulations.py          # 시뮬레이션 실행·조회
│       └── y_reports.py            # Y-Report 생성·조회
├── tasks/
│   ├── x_report_task.py            # Celery: X-Report (비동기 경로)
│   ├── simulation_task.py          # Celery: 시뮬레이션 실행
│   └── y_report_task.py            # Celery: Y-Report 생성
├── migrations/                     # Alembic 마이그레이션
├── tests/                          # pytest 테스트 스위트
├── scripts/                        # 로컬 검증 스크립트
├── celery_app.py                   # Celery 앱 설정
├── requirements.txt
├── README.md
└── WORKER_README.md                # Worker 배포 가이드
```

---

## 빠른 시작

### 1. 환경 설정

#### 1-1. Redis (Docker 권장)

```bash
docker run -d -p 6379:6379 --name redis-backbone redis:alpine
```

#### 1-2. `.env` 파일 작성

```env
# Database
DATABASE_URL=sqlite+aiosqlite:///./backbone.db   # 개발용
# DATABASE_URL=postgresql+asyncpg://user:pass@localhost/backbone  # 운영용

# Redis & Celery
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# OpenAI
OPENAI_API_KEY=sk-...

# Worker 인증
WORKER_API_KEY=your-secret-key

# 선택 옵션
SQL_ECHO=False
LOG_LEVEL=INFO
USE_OPENAI_MOCK_MODE=        # OK_9 | INVALID_COUNT (테스트용 Mock 모드)
```

#### 1-3. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 서버 실행

```bash
# 터미널 1 — FastAPI 서버
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 터미널 2 — Celery Worker
celery -A celery_app worker --loglevel=info --pool=solo   # Windows
# celery -A celery_app worker --loglevel=info             # Linux/Mac

# 터미널 3 (선택) — Flower 모니터링
celery -A celery_app flower --port=5555
# → http://localhost:5555
```

---

## API 엔드포인트

### 매장 (Stores)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/stores` | 매장 목록 (페이지네이션, 검색) |
| GET | `/stores/{id}` | 매장 상세 |
| GET | `/stores/{id}/documents` | 매장 문서 조회 |

### X-Report (동기 생성)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/x-reports` | X-Report 생성 (캐시 있으면 즉시 반환) |
| GET | `/x-reports/{id}` | X-Report 원본 조회 |
| GET | `/x-reports/{id}/view` | X-Report View DTO (프론트 최적화) |
| GET | `/x-reports/{id}/strategies` | 9개 전략 목록 |

### 시뮬레이션 (비동기 Job)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/simulations` | 시뮬레이션 Job 생성 |
| GET | `/simulations/{id}` | 시뮬레이션 결과 조회 |
| GET | `/simulations/{id}/view` | 시뮬레이션 View DTO |

### Y-Report (비동기 Job)

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/y-reports` | Y-Report Job 생성 |
| GET | `/y-reports/{id}` | Y-Report 원본 조회 |
| GET | `/y-reports/{id}/view` | Y-Report View DTO (비교 지표 포함) |

### Job 관리

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/jobs` | Job 생성 |
| GET | `/jobs/{id}` | Job 상태 조회 |
| GET | `/jobs` | Job 목록 (상태 필터 가능) |
| PATCH | `/jobs/{id}/status` | Job 상태 업데이트 (Worker 전용) |

> **API 문서:** http://127.0.0.1:8000/docs

---

## 사용 예시

### X-Report 생성 (동기)

```bash
# 1. X-Report 생성 (캐시 없으면 GPT 호출, 있으면 즉시 반환)
curl -X POST http://127.0.0.1:8000/x-reports \
  -H "Content-Type: application/json" \
  -d '{"store_source_id": "1839559562", "snapshot_version": "v1", "prompt_id": "default"}'

# 응답: {"id": 1, "status": "completed", "cached": false}

# 2. View DTO 조회 (프론트 렌더링용)
curl http://127.0.0.1:8000/x-reports/1/view
```

### 시뮬레이션 실행 (비동기)

```bash
# 1. 시뮬레이션 Job 생성
curl -X POST http://127.0.0.1:8000/simulations \
  -H "Content-Type: application/json" \
  -d '{
    "store_source_id": "1839559562",
    "selected_strategy_ids": ["S1_A", "S2_B", "S3_C"],
    "days": 7,
    "seed": 42
  }'

# 응답: {"id": 1, "status": "pending"}

# 2. 상태 폴링
curl http://127.0.0.1:8000/simulations/1
# → {"status": "processing", "progress": 45}
# → {"status": "completed", "progress": 100}
```

### Y-Report 생성 (비동기)

```bash
# 1. Y-Report Job 생성 (시뮬레이션 완료 후)
curl -X POST http://127.0.0.1:8000/y-reports \
  -H "Content-Type: application/json" \
  -d '{"simulation_id": 1, "selected_strategy_ids": ["S1_A"]}'

# 응답: {"jobId": 3}

# 2. Job 상태 확인
curl http://127.0.0.1:8000/jobs/3
# → {"status": "completed", "result_id": 2}

# 3. Y-Report View DTO 조회
curl http://127.0.0.1:8000/y-reports/2/view
```

---

## 데이터베이스 스키마

```
jobs
├── id, celery_task_id
├── job_type         (x_report | simulation | y_report)
├── status           (pending → processing → completed | failed)
├── store_source_id, snapshot_version, prompt_id
├── result_id        (FK → 완료된 리포트 ID)
├── progress (0-100), error_message
└── created_at, updated_at, started_at, completed_at

stores
├── id, source_id (business key), name, address
├── lat, lng, sentiment, revenue, grade
└── store_documents (1:N)

x_reports
├── id, store_id (FK), snapshot_id (FK), prompt_id
├── status, content_text (마크다운 전문)
├── config_json (전략 3×3 구조)
├── model_meta (모델명, 토큰 사용량)
├── unique (store_id, snapshot_id, prompt_id)  ← 캐시 키
└── created_at, finished_at, error_message

strategies               ← x_report 1:9
├── id, x_report_id (FK)
├── solution_group (1-3), action_idx (1-3)
├── title, description
├── execution, effect    ← 실행 가이드 & 기대 효과
├── config (JSON), impact_mapping (JSON)
└── position

simulations
├── id, store_source_id, x_report_id (FK)
├── selected_strategy_ids (JSON 배열)
├── days, seed, settings_json
├── input_json, output_json (before/after 통계)
└── status, created_at, finished_at, error_message

y_reports
├── id, store_source_id
├── x_report_id (FK), simulation_id (FK)
├── input_json, output_json (비교 지표)
├── markdown (보고서 전문)
└── status, created_at, finished_at, error_message
```

---

## 주요 설계 포인트

| 항목 | 설명 |
|---|---|
| **X-Report 동기 처리** | 백엔드 동기 구조 — GPT 호출 완료 후 즉시 반환, Job Queue 없이 단순화 |
| **Simulation/Y-Report 비동기** | Celery + Redis로 장시간 작업 비동기 처리, 프론트 폴링 |
| **Job 상태 3단계** | PENDING → PROCESSING → COMPLETED/FAILED (트랜잭션 원자성 보장) |
| **GPT 2단계 파이프라인** | Step1: 분석 (temp=0.1, 결정론적) → Step2: 전략 JSON 생성 (temp=0.7) |
| **JSON-first 파서** | GPT JSON 모드 우선 파싱, 실패 시 텍스트 fallback으로 9개 전략 추출 보장 |
| **트랜잭션 원자성** | X-Report + 9개 Strategy 단일 트랜잭션 저장 (partial save 없음) |
| **캐시 전략** | (store_id, snapshot_id, prompt_id) UNIQUE 제약으로 중복 생성 방지 |
| **ORM 이식성** | ORM 모델: Integer PK, String(36) UUID, JSON → 테스트는 SQLite, 운영은 PostgreSQL |
| **View Service** | 프론트 최적화 DTO 빌더 분리 (x_report_view_service, y_report_view_service) |
| **Worker 직접 DB 접근** | HTTP 레이어 제거, asyncio.run()으로 DB 직접 접근 → 지연 최소화 |
| **확장성** | GPU 머신에 Worker만 추가 배포로 병렬 처리 수평 확장 가능 |

---

## 테스트

```bash
# 단위 테스트 (in-memory SQLite, DB/Worker 불필요)
pytest tests/ -v

# JSON-first 파서 회귀 테스트
pytest tests/test_strategy_json_first.py -v

# 에러 핸들링 검증 테스트
pytest tests/test_error_handling.py -v

# 로컬 검증 스크립트 (서버/Worker 없이 실행)
USE_OPENAI_MOCK_MODE=OK_9 python scripts/verify_job_failure_propagation.py
python scripts/test_atomicity.py
```

자세한 로컬 검증 방법: [docs/LOCAL_VERIFY.md](docs/LOCAL_VERIFY.md)

---

## Troubleshooting

### Redis 연결 오류

```
Error: [Errno 10061] No connection could be made
```

```bash
docker start redis-backbone   # 또는
redis-cli ping
```

### Celery Worker 모듈 오류

```
Error: No module named 'celery_app'
```

```bash
cd c:/Users/user/backend
celery -A celery_app worker --loglevel=info --pool=solo
```

### OpenAI API Key 오류

```
Error: OPENAI_API_KEY not set in settings
```

`.env` 파일에 `OPENAI_API_KEY=sk-...` 추가

### pytest-asyncio 오류

```
PytestUnraisableExceptionWarning / mode 관련 오류
```

```bash
pip install pytest-asyncio==0.23.8 --force-reinstall --no-deps
```

---

## 완료된 작업 ✅

- [x] X-Report 생성 (동기, GPT-4-turbo 2단계 파이프라인)
- [x] Strategy 추출 파서 (JSON-first + 텍스트 fallback, 9개 보장)
- [x] Simulation Task (Celery 비동기, before/after 출력)
- [x] Y-Report Task (Celery 비동기, 전후 비교 분석)
- [x] Job 실패 전파 및 에러 핸들링 (프론트까지 전달)
- [x] 트랜잭션 원자성 보장 (TX1→TX2→TX3 3단계 모델)
- [x] View Service 분리 (x_report_view, y_report_view)
- [x] ORM 이식성 (SQLite 테스트 ↔ PostgreSQL 운영)
- [x] 로컬 검증 스크립트 (서버/Worker 없이 E2E 검증)
- [x] Worker API Key 인증

## 진행 예정 📋

- [ ] GPU 머신 Worker 배포
- [ ] Production PostgreSQL 환경 설정
- [ ] CI/CD 파이프라인 (GitHub Actions)
- [ ] 모니터링 (Sentry, Prometheus + Grafana)
- [ ] 사용자 인증 (JWT)
- [ ] Rate Limiting

---

## 라이선스

MIT
