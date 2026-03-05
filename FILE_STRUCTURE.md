# 백엔드 파일 구조 (FILE_STRUCTURE.md)

이 문서는 `backend` 프로젝트의 디렉토리와 파일 구조에 대한 상세 설명을 제공합니다.

## 디렉토리 개요

```text
backend/
├── app/                        # 메인 FastAPI 애플리케이션 로직
│   ├── api/                    # API 라우트 정의
│   │   └── routers/            # 리소스별 엔드포인트 핸들러
│   ├── core/                   # 공통 설정 및 유틸리티
│   ├── db/                     # 데이터베이스 설정 및 세션 관리
│   ├── models/                 # SQLAlchemy ORM 모델 (PostgreSQL/SQLite)
│   ├── repositories/           # 데이터 접근 계층 (DB 추상화)
│   ├── schemas/                # 검증을 위한 Pydantic v2 스키마
│   ├── services/               # 비즈니스 로직 및 복합 오케스트레이션
│   ├── engine/                 # DB 엔진 관리
│   ├── adapters/               # 외부 컴포넌트와의 통합 어댑터
│   └── main.py                 # FastAPI 앱 엔트리 포인트 및 초기화
├── tasks/                      # Celery 워커 작업 정의 (시뮬레이션 등)
├── migrations/                 # Alembic 데이터베이스 마이그레이션 스크립트
├── tests/                      # pytest 단위 및 통합 테스트 스위트
├── scripts/                    # 로컬 테스트 및 데이터 적재용 유틸리티 스크립트
├── celery_app.py               # Celery 애플리케이션 설정
├── alembic.ini                 # 데이터베이스 마이그레이션 설정 파일
├── requirements.txt            # 파이썬 의존성 패키지 목록
├── README.md                   # 프로젝트 전체 개요 및 설치 가이드
└── WORKER_README.md            # 워커(Worker) 배포를 위한 전용 가이드
```

## 상세 파일 설명

### /app/api/routers
- `admin.py`: 관리자 전용 기능을 위한 엔드포인트.
- `jobs.py`: 비동기 작업(Job)의 상태 확인 및 관리를 위한 엔드포인트.
- `stores.py`: 매장 검색, 목록 조회 및 상세 정보 엔드포인트.
- `x_reports.py`: AI 기반 X-Report(진단) 생성 및 조회.
- `simulations.py`: 에이전트 시뮬레이션 시작 및 결과 조회.
- `y_reports.py`: 비교 분석 Y-Report 생성.

### /app/models
- `store.py`: 위치, 매출, 감성 분석 등 매장 데이터 모델.
- `x_report.py`: AI가 생성한 진단 보고서 항목.
- `strategy.py`: X-Report와 연결된 구체적인 실행 전략.
- `simulation.py`: 에이전트 기반 시뮬레이션 설정 및 결과 데이터.
- `y_report.py`: 최종 비교 보고서 항목.
- `job.py`: 비동기 작업의 상태(Pending, Processing, Completed 등) 추적.

### /app/services
- `x_report_service.py`: GPT 분석 및 전략 생성을 조율.
- `simulation_service.py`: 에이전트 시뮬레이션 실행을 위한 논리적 브릿지.
- `y_report_service.py`: 시뮬레이션 결과를 최종 보고서로 가공.
- `llm_gpt_provider.py`: OpenAI API와의 직접적인 통신 담당.
- `store_grade_service.py`: 매장의 백분위 및 등급 산출 로직.

### /tasks
- `x_report_task.py`: 비동기 X-Report 생성을 위한 Celery 작업.
- `simulation_task.py`: 대규모 에이전트 시뮬레이션을 수행하는 고부하 작업.
- `y_report_task.py`: 최종 비교 보고서를 컴파일하고 생성하는 작업.

### 루트 파일
- `celery_app.py`: 워커가 작업을 가져오기 위해 사용하는 Celery 인스턴스 정의.
- `main.py`: FastAPI 설정, CORS 설정 및 모든 라우터 포함.
- `alembic.ini`: 데이터베이스 스키마 업데이트 관리를 위해 Alembic에서 사용.
