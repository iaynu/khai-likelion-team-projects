# data-ETL — 리뷰 분석 ETL 노트북

매장 리뷰 원문을 LLM으로 분석해, 서비스가 사용하는 **구조화 JSON**으로 바꾸는 Google Colab 노트북 모음입니다.
GPU(Colab A100)에서 LLM을 돌립니다. 리뷰 분석 노트북은 `LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct`를 4bit 양자화로 올려 사용합니다.

| 노트북 | 설명 |
|---|---|
| `review_analysis_json.ipynb` | 망원동 매장 리뷰를 분석해 매장별 리포트 JSON을 만듭니다. 중간 결과를 체크포인트 파일에 저장하고, 중복 ID·누락 ID를 점검한 뒤 병합합니다. LLM 출력에서 첫 JSON 객체만 안전하게 추출하는 함수를 씁니다. |
| `상권_1차_LM(JSON변환)_Final.ipynb` | 상권(시장) 분석 결과를 JSON으로 변환하고 보정하는 최종본입니다. |

## 실행

1. 노트북을 Colab에서 엽니다. 첫 번째 노트북에는 상단에 "Open in Colab" 배지가 있습니다.
2. 런타임을 GPU(A100 권장)로 설정합니다.
3. 첫 셀이 `transformers`, `accelerate`, `bitsandbytes` 버전을 맞춰 다시 설치합니다. 설치 후 런타임을 재시작해야 할 수 있습니다.
4. 입력 데이터는 Google Drive에 두고 노트북 안의 경로를 자신의 환경에 맞게 고칩니다. 입력 데이터는 저장소에 포함되어 있지 않습니다.

## 서비스 안에서의 위치

```
리뷰 원문 ─▶ data-ETL (EXAONE 3.5) ─▶ 매장별 분석 JSON ─▶ backend / agent-sim
```
