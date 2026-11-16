# llm-report — X-Report 생성 파이프라인

lovelop의 **X-Report**(매장 진단 리포트 + 시뮬레이션에 쓸 전략 솔루션 목록)를 만드는 GPT 파이프라인의 프로토타입입니다.
같은 로직이 서비스의 [`backend`](../backend)에 `app/services/llm_gpt_provider.py`로 이식되어 있습니다.

## 동작 방식

매장 한 곳의 데이터를 읽어 GPT를 두 번 호출합니다.

| 단계 | 목적 | temperature | 프롬프트 |
|---|---|---|---|
| 1. 분석 | 리뷰·인구·매출·업종·위험 요소 5가지 데이터를 종합해 약점과 경쟁 우위를 도출 | 0.1 | `ANALYSIS_*` |
| 2. 생성 | 분석 결과를 바탕으로 전략 카테고리 3개를 고르고, 카테고리마다 솔루션 2~4개(총 6~12개)를 제안 | 0.7 | `REPORT_*` |

1단계 프롬프트에는 다음 안전장치가 들어 있습니다.
- 20253년 같은 비정상 수치는 노이즈로 보고 제외하거나 교정합니다.
- 유동인구 피크와 매출 피크가 어긋나면 원인에 대한 가설을 세웁니다.
- 데이터에 없는 숫자는 만들지 않고 "정보 부족"으로 표기합니다.

2단계의 전략 카테고리는 `공간 경험`, `제품 매력`, `접점 확대`, `재방문 락인`, `브랜드 인지` 중에서 고릅니다.

```
매장 JSON + 인구 데이터 ─▶ [1. 분석] ─▶ 분석 결과 ─▶ [2. 생성] ─▶ X-Report (.md / .json)
```

## 파일

| 파일 | 설명 |
|---|---|
| `report_engine.py` | `GPTReportGenerator` 클래스와 실행 진입점. 매장 JSON 로드, 두 단계 호출, 결과 저장 |
| `prompts.py` | 분석용·생성용 시스템/유저 프롬프트 템플릿 |

## 실행

```bash
pip install openai python-dotenv
```

`.env`에 키를 설정합니다.

```env
OPENAI_API_KEY=your-api-key-here
OPENAI_MODEL_NAME=gpt-5.2   # 생략하면 gpt-5.2
```

```bash
python report_engine.py
# 🔎 리포트를 생성할 매장명을 입력하세요: <매장명>
```

결과는 `generated_reports/{매장명}_report.md`와 `{매장명}_result.json`으로 저장됩니다.
입력 데이터(매장별 JSON 디렉터리 `split_by_store_id_ver3`, 인구 데이터 `인구_DB.json`)는 `agent-sim/data/raw`에서 가져오며 저장소에 포함되어 있지 않습니다. `report_engine.py` 하단 실행부의 경로가 작성자 PC의 절대 경로로 박혀 있으므로, 실행 전에 자신의 경로로 바꿔야 합니다.
