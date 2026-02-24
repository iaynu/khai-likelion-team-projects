# Frontend API Contract

프론트엔드(Next.js)가 이 백엔드와 연동할 때 지켜야 하는 계약을 정의한다.

---

## 1. Base URL 설정

### 환경변수 (Next.js)

```env
# .env.local (로컬 개발)
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# Vercel Preview / Production
NEXT_PUBLIC_API_BASE_URL=https://your-backend-domain.com
```

### API Prefix

백엔드는 **버전 prefix 없음**. 모든 endpoint는 root에 직접 위치한다.

```
${NEXT_PUBLIC_API_BASE_URL}/simulations        ← not /api/v1/simulations
${NEXT_PUBLIC_API_BASE_URL}/y-reports
${NEXT_PUBLIC_API_BASE_URL}/jobs/{id}
```

### 주요 Endpoint 목록

| Method | Path | 용도 |
|--------|------|------|
| `POST` | `/simulations` | 시뮬레이션 Job 생성 + Celery enqueue |
| `GET`  | `/simulations/{sim_id}` | 시뮬레이션 결과 조회 |
| `POST` | `/y-reports` | Y-Report Job 생성 + Celery enqueue |
| `GET`  | `/y-reports/{y_report_id}` | Y-Report 결과 조회 |
| `GET`  | `/jobs/{job_id}` | Job 상태 폴링 (핵심) |
| `GET`  | `/health` | 서버 상태 확인 |

---

## 2. Job 폴링 계약

프론트는 `POST /simulations` 또는 `POST /y-reports` 응답의 `job_id`로
`GET /jobs/{job_id}`를 폴링하여 진행 상태를 확인한다.

### POST /simulations 응답

```json
{
  "job_id": 1,
  "sim_id": 1,
  "status": "pending"
}
```

### POST /y-reports 응답

```json
{
  "job_id": 2,
  "y_report_id": 1,
  "status": "pending"
}
```

### GET /jobs/{id} 응답 — 필수 필드

```json
{
  "id": 16,
  "job_type": "simulation",
  "status": "failed",
  "store_source_id": "돼지야",
  "result_id": null,
  "error_message": "[BACKEND_TIMEOUT] 시뮬레이션 시간 초과 (120s) | Context: store=돼지야, sim_id=16, job_id=16",
  "progress": 5,
  "created_at": "2026-02-23T02:41:54",
  "updated_at": "2026-02-23T02:41:54",
  "started_at": "2026-02-23T02:41:54.268383",
  "completed_at": null,
  "celery_task_id": null,
  "snapshot_version": null,
  "prompt_id": null
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | `string` | `pending` \| `processing` \| `completed` \| `failed` |
| `result_id` | `int \| null` | completed 시 sim_id 또는 y_report_id. 나머지는 null |
| `error_message` | `string \| null` | failed 시 `[ERROR_CODE] message \| Context: ...`. 나머지는 null |
| `progress` | `int \| null` | 0–100. 미지원 구간은 null |

**폴링 로직 권장**:
- 간격: 2–5초
- 종료 조건: `status === "completed"` 또는 `status === "failed"`
- completed → `result_id`로 결과 조회 API 호출

---

## 3. 에러 코드 (ERROR_CODE) — i18n 매핑

### 추출 규칙

`error_message` 필드에서 정규식으로 ERROR_CODE를 추출한다:

```js
const match = errorMessage.match(/^\[([A-Z_]+)\]/);
const code = match ? match[1] : "UNKNOWN_ERROR";
```

매칭 실패(null) 또는 `UNKNOWN_ERROR` 시 공통 fallback 메시지를 사용한다.

### 에러 코드 테이블

| ERROR_CODE | 발생 조건 | 사용자 메시지 (예시) |
|------------|-----------|---------------------|
| `INVALID_INPUT` | Simulation 미존재·COMPLETED 아님·before/after dir 없음 | "입력값이 올바르지 않습니다. 시뮬레이션 상태를 확인해 주세요." |
| `MISSING_DATA` | before_dir 또는 after_dir 경로가 실제로 존재하지 않음 | "분석에 필요한 데이터를 찾을 수 없습니다. 시뮬레이션을 다시 실행해 주세요." |
| `BACKEND_TIMEOUT` | asyncio.TimeoutError — 엔진 실행 시간 초과 | "처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요." |
| `STRATEGY_PARSE_COUNT_MISMATCH` | X-Report 전략 파싱 결과가 예상 수량과 다름 | "전략 생성 도중 오류가 발생했습니다. 다시 시도해 주세요." |
| `UNKNOWN_ERROR` | 위 코드에 해당하지 않는 예상치 못한 예외 | "알 수 없는 오류가 발생했습니다. 지속되면 관리자에게 문의해 주세요." |
| `VALIDATION_ERROR` | 요청 파라미터 검증 실패 (422) | "입력 형식이 올바르지 않습니다: {detail}" |
| `INTERNAL_ERROR` | 서버 내부 오류 (500) | "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요." |

### Context 처리

`error_message`의 ` | Context: ...` 뒤 부분은 **내부 디버그 정보**다.
사용자에게 노출하지 말 것. 추출 예시:

```js
const [codePart, contextPart] = errorMessage.split(" | Context: ");
const userMessage = i18n[code] ?? i18n["UNKNOWN_ERROR"];
// contextPart는 로그에만 기록
```

---

## 4. 422 Validation Error 포맷

FastAPI 기본 422 응답을 `[VALIDATION_ERROR]` 포맷으로 통일했다.

```json
{
  "error_message": "[VALIDATION_ERROR] store_source_id: Field required; days: Input should be less than or equal to 30",
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "store_source_id"],
      "msg": "Field required",
      "input": {"days": 99999}
    }
  ]
}
```

- `error_message`: 프론트 공통 에러 UI에서 ERROR_CODE 추출 가능
- `detail`: 개발자 디버깅용. 프로덕션 UI에는 노출 금지

---

## 5. CORS 설정

### 현재 허용 Origin

| 환경 | Origin |
|------|--------|
| 로컬 개발 | `http://localhost:3000` |
| Vercel Preview | `^https://frontend-.*\.vercel\.app$` (정규식) |

### 환경변수로 Override

```env
# .env (백엔드)
# 쉼표 구분 또는 JSON 배열
CORS_ALLOWED_ORIGINS=http://localhost:3000,https://frontend-ten-beta-37.vercel.app

# Vercel Preview 정규식 (프로젝트명 고정 권장)
CORS_ALLOWED_ORIGIN_REGEX=^https://frontend-ten-beta-37\.vercel\.app$
```

**보안 권장**:
- prod/staging: `CORS_ALLOWED_ORIGIN_REGEX`를 프로젝트 slug로 좁힐 것
  ```
  ^https://frontend-ten-beta-37\.vercel\.app$
  ```
- 브랜치 Preview가 많으면: `^https://frontend-ten-beta-37-.*-team\.vercel\.app$`

---

## 6. output_json 내 서버 경로 필드 (사용 금지)

`GET /simulations/{id}` 및 `GET /y-reports/{id}` 응답의 `output_json` 내에
다음 필드는 **서버 filesystem 경로**를 포함한다. 프론트에서 직접 사용하지 말 것.

| 리소스 | 필드 경로 | 설명 |
|--------|-----------|------|
| Simulation | `output_json.before.output_dir` | 서버 로컬 경로 (예: `/data/sim/before`) |
| Simulation | `output_json.after.output_dir` | 서버 로컬 경로 |
| Y-Report | `output_json.adapter_meta.before_dir` | 서버 로컬 경로 |
| Y-Report | `output_json.adapter_meta.after_dir` | 서버 로컬 경로 |
| Y-Report | `output_json.adapter_meta.report_path` | 서버 로컬 .md 파일 경로 |

**대신 사용할 필드**:
- Simulation: `summary` (computed field) → `timing_ms`, `engine`, `run_id` 등
- Y-Report: `summary` (computed field) → `timing_ms`, `engine`, `visit_change_pct` 등
- Y-Report: `markdown` → 보고서 전문 (마크다운 텍스트)
- Y-Report: `output_json.structured` → 수치 지표 dict

---

## 7. Simulation / Y-Report 상태값

**Simulation / Y-Report status** (대문자):

```
PENDING → PROCESSING → COMPLETED
                     ↘ FAILED
```

**Job status** (소문자):

```
pending → processing → completed
                     ↘ failed
```

Simulation/Y-Report의 `status`와 Job의 `status`는 **대소문자가 다르다**.
Job 폴링 시 소문자(`"completed"`)로 비교할 것.

---

## 8. camelCase 정책 (BACKEND-0 추가)

### 규칙

| 항목 | 정책 |
|------|------|
| JSON 응답 키 | 모두 `camelCase` |
| JSON 요청 키 | `camelCase` 또는 `snake_case` 모두 허용 (`populate_by_name=True`) |
| Python 모델 필드 | `snake_case` + `alias_generator=to_camel` 자동 변환 |

### 변환 예시

| Python 필드명 | JSON 키 |
|---------------|---------|
| `store_source_id` | `storeSourceId` |
| `rank_percent` | `rankPercent` |
| `radar_data` | `radarData` |
| `full_report` | `fullReport` |
| `rating_distribution` | `ratingDistribution` |
| `llm_summary` | `llmSummary` |
| `side_effects` | `sideEffects` |

**예외**: 기존 `/jobs/{id}`, `/simulations/{id}`, `/y-reports/{id}` 엔드포인트는
`snake_case` 유지 (하위 호환). View DTO 전용 엔드포인트만 `camelCase`.

---

## 9. Null / 빈 리스트 정책 (BACKEND-0 추가)

| 상황 | 반환값 |
|------|--------|
| 아직 생성되지 않은 단일 객체 | `null` |
| **아직 생성되지 않은 리스트** | `[]` (**절대 `null` 아님**) |
| 분석 중(pending/processing) 상태의 View 리스트 필드 | `[]` |

모든 리스트 필드는 `Field(default_factory=list)`로 선언해 null 방지.

---

## 10. 매장 목록 View ✅ BACKEND-1 구현 완료

### `GET /stores`

**쿼리 파라미터**

| 파라미터 | 타입 | 기본값 | 설명 |
|---------|------|--------|------|
| `q` | string | - | name / area_code 부분 검색 (대소문자 무시) |
| `limit` | int | 50 | 페이지 크기 (1–200, 초과 시 200으로 clamp) |
| `offset` | int | 0 | 오프셋 (≥ 0, 음수 시 422) |
| `sort` | string | `name` | 정렬 기준 (현재 `name`만 지원) |

**응답: `StoreListViewOut`**

```json
{
  "items": [
    {
      "id": "돼지야",
      "name": "돼지야",
      "address": "서울 마포구",
      "lat": null,
      "lng": null,
      "sentiment": 0.85,
      "revenue": null,
      "grade": null,
      "rankPercent": null
    }
  ],
  "total": 726,
  "limit": 50,
  "offset": 0
}
```

| 필드 | 타입 | 출처 | 현황 |
|------|------|------|------|
| `id` | `string` | `Store.store_source_id` | ✅ |
| `name` | `string` | `Store.store_name` | ✅ |
| `address` | `string\|null` | `Store.area_code` (fallback) | ⚠️ area_code 수준 |
| `lat`, `lng` | `float\|null` | `store_documents.payload` 파싱 | ⚠️ 현재 DB에 좌표 없음 → null |
| `sentiment` | `float\|null` | `store_documents.payload.analysis_reports[0].review_metrics.overall_sentiment.score` | ✅ 추출됨 |
| `revenue` | `int\|null` | `store_documents.payload` | ⚠️ 구조 미확정 → null |
| `grade` | `string\|null` | X-Report 구조화 데이터 | ⚠️ 미구현 → null |
| `rankPercent` | `int\|null` | X-Report 구조화 데이터 | ⚠️ 미구현 → null |

### lat/lng null 처리 정책

| 상황 | 동작 |
|------|------|
| 페이지 내 null 비율 < 80% | null 좌표 매장을 응답에서 **제외** |
| 페이지 내 null 비율 ≥ 80% | 전체 포함 (fallback) + 서버 `WARNING` 로그 |

현재 DB에 좌표 데이터가 없으므로 100% null → fallback 모드로 동작.
추후 `store_documents.payload`에 `lat`/`lng` 필드를 추가하면 자동으로 파싱됨.

### pagination 규칙

- `limit`: 응답에 **실제 적용된 limit** 값 포함 (프론트 다음 페이지 계산용)
- `offset`: 응답에 포함
- `total`: `q` 필터 적용 후 전체 개수 (offset/limit 미적용)

---

## 11. X-Report View DTO (BACKEND-2 ✅ 완료)

### `GET /x-reports/{id}/view`

X-Report 상세 View. 프론트 XReport 페이지 전용.

**구현 위치**:
- 서비스: `app/services/x_report_view_service.py` → `build_x_report_view()`
- 엔드포인트: `app/api/routers/x_reports.py` → `GET /{report_id}/view`
- 스키마: `app/schemas/x_report.py` → `XReportViewOut`
- 스모크 테스트: `scripts/smoke_x_report_view.py`

**solutions 우선순위**:
1. `config_json['categories']` (P1: 구조화된 포맷)
2. `config_json['strategies']` (P2: 현재 mock 포맷)
3. DB `strategies` 테이블 rows (P3: DB 기반)

**radarData** 주제: `["맛", "서비스", "분위기", "가격", "위생"]` (5개 축)
- 데이터 없을 시 A=0 플레이스홀더 반환

**응답: `XReportViewOut`**

```json
{
  "id": 1,
  "storeId": "uuid-string",
  "status": "SUCCEEDED",
  "grade": "A",
  "rankPercent": 8,
  "radarData": [
    { "subject": "맛", "A": 90, "fullMark": 100, "reason": "매우 좋음" },
    { "subject": "서비스", "A": 80, "fullMark": 100, "reason": "매우 좋음" },
    { "subject": "청결도", "A": 60, "fullMark": 100, "reason": "보통" },
    { "subject": "가성비", "A": 70, "fullMark": 100, "reason": "좋음" },
    { "subject": "분위기", "A": 70, "fullMark": 100, "reason": "보통" },
    { "subject": "접근성", "A": 50, "fullMark": 100, "reason": "보통" }
  ],
  "keywords": [
    { "text": "고기", "sentiment": "neutral" },
    { "text": "위생", "sentiment": "negative" }
  ],
  "solutions": [
    {
      "category": "공간 경험",
      "title": "청결 3포인트 표준",
      "desc": "데이터 기반 맞춤형 전략",
      "execution": "2시간 단위 점검표...",
      "effect": ""
    }
  ],
  "description": "돼지야는 맛과 가성비...",
  "fullReport": "# 🏮 [X-report] ..."
}
```

---

## 12. Y-Report View DTO (BACKEND-3 ✅ 완료)

### `GET /y-reports/{id}/view`

Y-Report 상세 View. 프론트 YReport 페이지 전용.
`status`가 `COMPLETED`가 아닌 경우 metric 필드들은 `null` 또는 `[]`.

**구현 위치**:
- 서비스: `app/services/y_report_view_service.py` → `build_y_report_view()`
- 엔드포인트: `app/api/routers/y_reports.py` → `GET /{y_report_id}/view`
- 스키마: `app/schemas/y_report.py` → `YReportViewOut`
- 스모크 테스트: `scripts/smoke_y_report_view.py --id <ID>`

**sim1/sim2 구분 우선순위**:
1. `output_json.structured.{field}_before / _after` 패턴 (P1)
2. `scenarios[].scenario_type == "base"/"strategy"` (P2)
3. `is_base_case / isBaseline` 플래그 (P3)
4. 기본값(0) fallback + WARNING 로그 (P4)

**Null 금지 정책**: `hourlyTraffic`, `sideEffects`, `tradeoffs`, `generation`, `purpose`, `radar`, `agentType`, `gender` → 항상 `[]`
**항상 구조 제공**: `overview`, `keywords`, `retention`, `riskScore` → 값 없으면 기본값(0) 포함 구조

**에러 처리**:
- `404`: y_report_id 없음
- `500 [YREPORT_PARSE_ERROR]`: output_json 파싱 실패 + markdown 없음

**응답: `YReportViewOut`** (주요 필드)

| 필드 | 타입 | 설명 |
|------|------|------|
| `overview` | `object \| null` | `{sim1, sim2}` — 방문수/시장점유율 |
| `keywords` | `object \| null` | `{sim1[], sim2[]}` — 워드클라우드 키워드 |
| `ratingDistribution` | `object \| null` | `{taste, value, atmosphere}` — KDE 평점 분포 |
| `ratingSummary` | `object \| null` | `{sim1, sim2}` — 평균 평점/만족도 |
| `hourlyTraffic` | `array` | `{slot, sim1, sim2}[]` — 시간대별 트래픽 |
| `peakSlot` | `object \| null` | `{sim1, sim2}` — 피크 시간대 |
| `generation` | `array` | `{gen, sim1, sim2}[]` — 세대별 증감 |
| `purpose` | `array` | `{type, sim1Pct, sim2Pct, sim1Sat, sim2Sat}[]` — 방문 목적 |
| `retention` | `object \| null` | 재방문율 지표 |
| `radar` | `array` | `{metric, unit, targetBefore, targetAfter, comp1-3}[]` — 경쟁사 벤치마크 |
| `radarStores` | `object \| null` | `{comp1, comp2, comp3}` — 경쟁사명 |
| `agentType` | `array` | `{type, sim1, sim2}[]` — 에이전트 유형 |
| `gender` | `array` | `{label, sim1, sim2}[]` — 성별 구성 |
| `crosstab` | `object \| null` | 세대×방문목적 크로스탭 |
| `sideEffects` | `array` | `{type, metric, change, unit, detail}[]` — 역효과 |
| `tradeoffs` | `array` | `{gain, gainVal, loss, lossVal}[]` — 트레이드오프 |
| `riskScore` | `object \| null` | 솔루션 안전성 점수 |
| `llmSummary` | `string \| null` | LLM 종합 평가 (Markdown) |

---

## 13. 구현 로드맵

| 단계 | 내용 | 상태 |
|------|------|------|
| BACKEND-0 | View DTO 스키마 정의 + 이 문서 | ✅ 완료 |
| BACKEND-1 | `GET /stores` 구현 (`StoreListViewOut`) | ✅ 완료 |
| BACKEND-2 | `GET /x-reports/{id}/view` 구현 (`XReportViewOut`) | ✅ 완료 |
| BACKEND-3 | `GET /y-reports/{id}/view` 구현 (`YReportViewOut`) | ✅ 완료 |
| FRONTEND | API 클라이언트 레이어, mock 교체, 폴링 구현 | ⬜ 미구현 |
