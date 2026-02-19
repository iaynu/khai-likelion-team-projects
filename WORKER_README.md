# Worker 실행 가이드

Celery Worker를 사용한 비동기 X-Report 생성 시스템입니다.

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
│  (GPT-4)        │
└─────────────────┘
        ↓
┌─────────────────┐
│  POST /x-reports│
│  /save          │
└─────────────────┘
```

---

## 사전 준비

### 1. Redis 설치

**Windows (WSL 사용)**
```bash
# WSL에서 Redis 설치
sudo apt update
sudo apt install redis-server

# Redis 시작
sudo service redis-server start

# 확인
redis-cli ping  # 응답: PONG
```

**Windows (MSI 설치)**
- https://github.com/microsoftarchive/redis/releases
- Redis-x64-3.0.504.msi 다운로드 & 설치

### 2. 환경 변수 설정

`.env` 파일에 다음 추가:
```env
# Redis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# API Keys
WORKER_API_KEY=your-secret-worker-key
OPENAI_API_KEY=sk-...  # OpenAI API Key
```

### 3. 패키지 설치

```bash
cd c:/Users/user/backend
pip install celery redis flower openai
```

---

## 실행 방법

### 1. FastAPI 서버 실행 (터미널 1)

```bash
cd c:/Users/user/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 2. Celery Worker 실행 (터미널 2)

```bash
cd c:/Users/user/backend
celery -A celery_app worker --loglevel=info
```

**GPU 머신에서 실행:**
```bash
# GPU 머신 접속 후
celery -A celery_app worker --loglevel=info --concurrency=2
```

### 3. Flower 모니터링 (선택, 터미널 3)

```bash
cd c:/Users/user/backend
celery -A celery_app flower --port=5555
```

웹 브라우저: http://localhost:5555

---

## 사용 예시

### 1. Job 생성

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "x_report",
    "store_source_id": "1254472158",
    "snapshot_version": "v1",
    "prompt_id": "default"
  }'
```

응답:
```json
{
  "id": 1,
  "celery_task_id": "abc123...",
  "job_type": "x_report",
  "status": "pending",
  ...
}
```

### 2. Job 상태 확인

```bash
curl http://127.0.0.1:8000/jobs/1
```

응답:
```json
{
  "id": 1,
  "status": "completed",  # pending → processing → completed
  "result_id": 5,         # x_report_id
  "progress": 100
}
```

### 3. X-Report 조회

```bash
curl http://127.0.0.1:8000/x-reports/5
```

### 4. 12개 Strategy 조회

```bash
curl http://127.0.0.1:8000/x-reports/5/strategies
```

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

# 없으면 Redis 시작
sudo service redis-server start  # WSL
# 또는
redis-server  # Windows
```

### Celery Worker 오류
```
Error: No module named 'celery_app'
```

**해결:**
```bash
# 올바른 디렉토리에서 실행
cd c:/Users/user/backend
celery -A celery_app worker --loglevel=info
```

### OpenAI API 오류
```
Error: OPENAI_API_KEY not set
```

**해결:**
`.env` 파일에 API Key 추가

---

## 다음 단계

- [ ] Worker에서 실제 Store 데이터 조회 구현
- [ ] GPT Prompt 최적화
- [ ] Y-Report Task 추가
- [ ] Simulation Task 추가
- [ ] GPU 머신 배포
