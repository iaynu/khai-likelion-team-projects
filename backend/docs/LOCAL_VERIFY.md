# 로컬 검증 가이드

서버/Worker 없이 트랜잭션 원자성 및 에러 전파를 검증하는 스크립트 모음.

---

## PART 3 — 단일 서비스 진입점 통합 검증 (신규)

`generate_or_get_x_report()` 통합 후 아래 2가지 시나리오를 반드시 확인한다.

### 시나리오 1 — 동일 입력으로 2번 실행 시 FAILED 없이 캐시 히트

```bash
# 1회차
curl -X POST http://localhost:8000/x-reports \
  -H "Content-Type: application/json" \
  -d '{"store_source_id": "test_store", "snapshot_version": "v1", "prompt_id": "default"}'
# 기대: "status": "SUCCEEDED", "cached": false

# 2회차 — 동일 입력
curl -X POST http://localhost:8000/x-reports \
  -H "Content-Type: application/json" \
  -d '{"store_source_id": "test_store", "snapshot_version": "v1", "prompt_id": "default"}'
# 기대: "status": "SUCCEEDED", "cached": true  (FAILED 이면 버그)
```

### 시나리오 2 — FAILED 행이 있을 때 force_regenerate=false 로도 재생성

```bash
# Step 1: 정상 생성 후 DB에서 status = 'FAILED' 로 직접 변경
UPDATE x_reports SET status='FAILED'
WHERE store_id = (SELECT id FROM stores WHERE source_id='fail_test');

# Step 2: force_regenerate=false 로 재요청
curl -X POST http://localhost:8000/x-reports \
  -H "Content-Type: application/json" \
  -d '{"store_source_id": "fail_test", "snapshot_version": "v1", "prompt_id": "default"}'
# 기대: "status": "SUCCEEDED", "cached": false  (FAILED → SUCCEEDED 전환)
```

### 확인 포인트

| 항목 | 기대값 |
|------|--------|
| 2회차 동일 요청 | `cached: true`, `status: SUCCEEDED` |
| FAILED 행 재생성 | `cached: false`, `status: SUCCEEDED` |
| IntegrityError 흡수 | jobs 테이블에 FAILED 행 없음 |
| strategies 교체 | 재생성 후 x_report_id 기준 9개 신규 행 |

### 레거시 스크립트 현황

PART 3 이후 아래 스크립트는 `_save_via_service` import로 업데이트됨.
내부 호출 로직은 `generate_or_get_x_report()` 기반으로 재작성이 필요하다.

- `scripts/test_atomicity.py`
- `scripts/verify_job_failure_propagation.py`

---

---

## 빠른 실행

### 1. Job 실패 전파 검증 (E2E)

```bash
# 정상 케이스 (9개 솔루션 → Job COMPLETED)
USE_OPENAI_MOCK_MODE=OK_9 python scripts/verify_job_failure_propagation.py

# 실패 케이스 (6개 솔루션 → Job FAILED)
USE_OPENAI_MOCK_MODE=BAD_6 python scripts/verify_job_failure_propagation.py
```

### 2. 트랜잭션 원자성 검증

```bash
# Scenario A: 파싱 실패 시 rollback
# Scenario B: DB 실패 시뮬레이션 rollback
python scripts/test_atomicity.py
```

### 3. DB 직접 접근 유틸 검증

```bash
# get_async_session(), bulk_create(), create_completed() 검증
python scripts/test_db_direct_access.py
```

---

## 디버깅 옵션

### SQL 쿼리 로깅 활성화

```bash
# SQL 쿼리 전체 출력 (SQLAlchemy echo)
SQL_ECHO=1 USE_OPENAI_MOCK_MODE=OK_9 python scripts/verify_job_failure_propagation.py
```

**출력 예시:**
```sql
2024-02-21 10:23:45,123 INFO sqlalchemy.engine.Engine BEGIN (implicit)
2024-02-21 10:23:45,124 INFO sqlalchemy.engine.Engine INSERT INTO jobs (job_type, celery_task_id, status, ...) VALUES (?, ?, ?, ...)
2024-02-21 10:23:45,125 INFO sqlalchemy.engine.Engine [generated in 0.00012s] ('x_report', 'test-task-001', 'pending', ...)
2024-02-21 10:23:45,126 INFO sqlalchemy.engine.Engine COMMIT
```

### 애플리케이션 로그 레벨 조정

```bash
# DEBUG 레벨 로그 출력
LOG_LEVEL=DEBUG USE_OPENAI_MOCK_MODE=OK_9 python scripts/verify_job_failure_propagation.py
```

**출력 예시:**
```
[DEBUG] Job 1 updated: PENDING → PROCESSING
[DEBUG] Store found: id=550e8400-e29b-41d4-a716-446655440000, source_id=TEST_STORE_001
[DEBUG] XReport created: id=2, store_id=550e8400-e29b-41d4-a716-446655440000
[DEBUG] Strategies bulk created: count=9
[DEBUG] Job 1 updated: PROCESSING → COMPLETED, result_id=2
```

### 전체 디버그 모드 (SQL + LOG)

```bash
# SQL 쿼리 + 애플리케이션 로그 모두 출력
SQL_ECHO=1 LOG_LEVEL=DEBUG USE_OPENAI_MOCK_MODE=BAD_6 python scripts/verify_job_failure_propagation.py
```

---

## 환경 변수 레퍼런스

| 변수 | 설명 | 기본값 | 예시 |
|---|---|---|---|
| `USE_OPENAI_MOCK_MODE` | Mock 모드 (OK_9/BAD_6) | (없음) | `OK_9`, `BAD_6` |
| `SQL_ECHO` | SQL 쿼리 로깅 활성화 | `0` | `1` |
| `LOG_LEVEL` | 애플리케이션 로그 레벨 | `INFO` | `DEBUG`, `WARNING`, `ERROR` |
| `DATABASE_URL` | DB 연결 문자열 | SQLite in-memory | `sqlite+aiosqlite:///:memory:` |

**참고:** 검증 스크립트는 내부에서 `DATABASE_URL`을 `sqlite+aiosqlite:///:memory:`로 자동 설정합니다.

---

## 각 스크립트 상세 설명

### 1. `verify_job_failure_propagation.py`

**목적:** Celery Task → Job 실패 전파 E2E 검증

**검증 항목:**
- CASE OK_9: 9개 솔루션 → Job COMPLETED + XReport 생성 + 9개 Strategies 생성
- CASE BAD_6: 6개 솔루션 → ValueError 발생 → Job FAILED + XReport/Strategies 미생성

**트랜잭션 경계:**
- TX1: Job → PROCESSING (독립 트랜잭션)
- TX2: XReport + Strategies + Job COMPLETED (원자적)
- TX3: Job → FAILED (독립 트랜잭션, rollback 후)

**실행 예시:**
```bash
USE_OPENAI_MOCK_MODE=OK_9 python scripts/verify_job_failure_propagation.py
```

**정상 출력:**
```
================================================================================
CASE: OK_9 (9 solutions → Job COMPLETED)
================================================================================
[Setup] Test data created: job_id=1, store_id=..., snapshot_id=1
[Execute] Calling generate_x_report(job_id=1) with 9 solutions...
[Verify] Session B: Checking DB state after task execution...
  Job status: completed (expected) OK
  Job result_id: 2 OK
  XReport exists: id=2 OK
  Strategies count: 9 (expected) OK
[Cleanup] Deleted Strategy(9) -> XReport(1) -> Job(1) OK

[PASS] CASE OK_9: Job completed successfully with 9 strategies
```

**실패 케이스 출력:**
```
================================================================================
CASE: BAD_6 (6 solutions → Job FAILED)
================================================================================
[Setup] Test data created: job_id=2, store_id=..., snapshot_id=1
[Execute] Calling generate_x_report(job_id=2) with 6 solutions...
[ERROR] ValueError: Expected 9 solutions, got 6
[Verify] Session B: Checking DB state after task execution...
  Job status: failed (expected) OK
  Job error_message contains STRATEGY_PARSE_COUNT_MISMATCH OK
  XReports count: 0 (expected 0) OK
  Strategies count: 0 (expected 0) OK
[Cleanup] Deleted Job(1) OK

[PASS] CASE BAD_6: Job failed correctly, no partial save
```

---

### 2. `test_atomicity.py`

**목적:** 트랜잭션 원자성 검증 (rollback 동작 확인)

**검증 항목:**
- Scenario A: 파싱 실패 (6개 솔루션) → XReport/Strategy 미생성, Job FAILED
- Scenario B: DB 실패 시뮬레이션 (RuntimeError) → rollback 확인

**실행 예시:**
```bash
python scripts/test_atomicity.py
```

**정상 출력:**
```
================================================================================
TRANSACTION ATOMICITY VERIFICATION TEST
================================================================================
[Setup] Database schema + test data created
  Store: id=..., source_id=TEST_STORE_001
  Snapshot: id=1, version=v1.0.0
  Job: id=1, status=pending

================================================================================
Scenario A: Parsing Failure (ValueError: 6 solutions instead of 9)
================================================================================
...
[PASS] Scenario A: Atomicity verified - no partial save occurred

================================================================================
Scenario B: DB Failure Simulation (rollback during transaction)
================================================================================
...
[PASS] Scenario B: Rollback verified - partial transaction was reverted

================================================================================
ALL ATOMICITY TESTS PASSED OK
================================================================================

결론:
  1. 파싱 실패 시 XReport/Strategy가 생성되지 않음 (원자성 보장)
  2. 트랜잭션 중간 실패 시 모든 변경사항 rollback됨 (원자성 보장)
  3. 실패 기록은 별도 트랜잭션으로 Job FAILED 상태 저장됨
```

---

### 3. `test_db_direct_access.py`

**목적:** DB 직접 접근 유틸리티 함수 검증

**검증 항목:**
- Test 1: `get_async_session()` context manager 동작
- Test 2: `StrategyRepo.bulk_create()` 9개 strategies flush
- Test 3: `XReportRepo.create_completed()` XReport 생성
- Test 4: 트랜잭션 rollback 동작

**실행 예시:**
```bash
python scripts/test_db_direct_access.py
```

**정상 출력:**
```
[Setup] Database schema created

================================================================================
Test 1: get_async_session() context manager
================================================================================
Session created: <sqlalchemy.ext.asyncio.session.AsyncSession object at 0x...>
Session type: AsyncSession
PASS: Session context manager works

================================================================================
Test 2: StrategyRepo.bulk_create() with 9 strategies
================================================================================
XReport created: id=1
Strategies created: count=9
  [1] id=1, group=1, title=Strategy 1
  ...
  [9] id=9, group=3, title=Strategy 9
PASS: bulk_create() successfully created and flushed 9 strategies

...

================================================================================
ALL TESTS PASSED
================================================================================
```

---

## 예외 처리 및 디버깅

### Traceback 출력

모든 검증 스크립트는 예외 발생 시 자동으로 traceback을 출력합니다:

```python
except Exception as e:
    print(f"\n[ERROR] {e}")
    import traceback
    traceback.print_exc()
    return 1
```

**출력 예시:**
```
[ERROR] AssertionError: Expected 9 strategies, got 6

Traceback (most recent call last):
  File "scripts/verify_job_failure_propagation.py", line 245, in verify_case_ok_9
    assert strategy_count == 9, f"Expected 9 strategies, got {strategy_count}"
AssertionError: Expected 9 strategies, got 6
```

### 일반적인 오류 및 해결 방법

#### 1. `ModuleNotFoundError: No module named 'app'`

**원인:** 프로젝트 루트에서 실행하지 않음

**해결:**
```bash
cd C:/Users/user/backend
python scripts/verify_job_failure_propagation.py
```

#### 2. `ImportError: cannot import name 'get_async_session'`

**원인:** 코드 변경 후 Python 캐시 미갱신

**해결:**
```bash
# 캐시 삭제
find . -type d -name __pycache__ -exec rm -rf {} +
find . -name "*.pyc" -delete

# 또는 Windows에서
for /d /r . %d in (__pycache__) do @if exist "%d" rd /s /q "%d"
```

#### 3. SQL 쿼리가 출력되지 않음

**원인:** `SQL_ECHO=1` 환경 변수가 설정되지 않음

**해결:**
```bash
# Windows (Git Bash)
SQL_ECHO=1 python scripts/verify_job_failure_propagation.py

# Windows (PowerShell)
$env:SQL_ECHO="1"; python scripts/verify_job_failure_propagation.py

# Windows (CMD)
set SQL_ECHO=1 && python scripts/verify_job_failure_propagation.py
```

---

## 참고: 프로덕션 환경 검증

**주의:** 이 스크립트들은 개발/로컬 검증 전용입니다. 프로덕션 환경에서는:

1. **실제 서버/Worker 실행:**
   ```bash
   # FastAPI 서버
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

   # Celery Worker
   celery -A celery_app worker --loglevel=info
   ```

2. **실제 Job API 호출:**
   ```bash
   curl -X POST http://127.0.0.1:8000/jobs \
     -H "Content-Type: application/json" \
     -d '{"job_type": "x_report", "store_source_id": "...", "snapshot_version": "v1", "prompt_id": "default"}'
   ```

3. **실제 PostgreSQL 사용:**
   ```env
   DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/backbone
   ```

---

## 프론트 Contract 통합 스모크 (BACKEND-4)

서버 실행 후 아래 한 줄로 `/stores`, `/x-reports/{id}/view`, `/y-reports/{id}/view` 계약을 한번에 확인한다.

```bash
python scripts/smoke_contract_all.py --base-url http://localhost:8000 --stores-limit 3 --x-id 1 --y-id 5
```

---

## 관련 문서

- [README.md](../README.md): 프로젝트 개요 및 빠른 시작
- [tasks/x_report_task.py](../tasks/x_report_task.py): Celery Task 구현 (트랜잭션 경계 주석 참고)
- [app/db/session.py](../app/db/session.py): `get_async_session()` 구현
- [app/repositories/](../app/repositories/): `bulk_create()`, `create_completed()` 구현
