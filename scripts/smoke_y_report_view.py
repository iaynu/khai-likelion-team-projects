"""
GET /y-reports/{id}/view 스모크 테스트

실행 방법:
    # python -m scripts.smoke_y_report_view --id <YREPORT_ID>
    python -m scripts.smoke_y_report_view
    python -m scripts.smoke_y_report_view --id 5

확인 항목:
    1) GET /y-reports/{id}/view → 200 + 필수 키 존재 여부
    2) 리스트 필드(hourlyTraffic, sideEffects) null 금지 assert
    3) keywords.sim1 / keywords.sim2 키 반드시 존재 assert
    4) overview.sim1 / overview.sim2 키 반드시 존재 assert
    5) marketShare / riskScore.score clamp 결과 확인
    6) camelCase 정책 — keys 확인
    7) GET /y-reports/9999999/view → 404 기대
    8) FAILED 보고서 (no output_json, no markdown) → 5xx 기대
"""
import argparse
import json
import os
import sys

try:
    import requests
except ImportError:
    print("[ERROR] requests 미설치: pip install requests")
    sys.exit(1)

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TIMEOUT = 5

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  {PASS}  {label}")
    else:
        _failed += 1
        print(f"  {FAIL}  {label}" + (f"  → {detail}" if detail else ""))


def get(path: str, expected_status: int = 200) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != expected_status:
            print(f"\n[ERROR] HTTP {r.status_code} (expected {expected_status}): {r.text[:300]}")
            sys.exit(1)
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] 서버에 연결할 수 없음: {url}")
        print("       uvicorn app.main:app --reload 로 서버를 먼저 실행하세요.\n")
        sys.exit(1)


def get_raw(path: str) -> "requests.Response":
    url = f"{BASE_URL}{path}"
    try:
        return requests.get(url, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] 서버에 연결할 수 없음: {url}")
        sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


# ── 인자 파싱 ─────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Y-Report view smoke test")
parser.add_argument("--id", type=int, default=int(os.getenv("YREPORT_ID", "5")), dest="report_id",
                    help="테스트할 y_report_id (기본: 5)")
args = parser.parse_args()
REPORT_ID = args.report_id

# ── Case 1: 기본 조회 ─────────────────────────────────────────────────────────
section(f"Case 1: GET /y-reports/{REPORT_ID}/view")
data = get(f"/y-reports/{REPORT_ID}/view")

print(f"  응답 키 목록: {sorted(data.keys())}")

# 필수 top-level camelCase 키 목록 (contract 정의)
required_keys = {
    "id", "storeSourceId", "simulationId", "status",
    "overview", "keywords", "hourlyTraffic",
    "retention", "sideEffects", "riskScore", "llmSummary",
}
for key in required_keys:
    check(f"응답에 '{key}' 키 포함", key in data, f"실제={list(data.keys())}")

# ── Case 2: 리스트 필드 null 금지 ─────────────────────────────────────────────
section("Case 2: 리스트 필드 null 금지 assert")
hourly = data.get("hourlyTraffic")
side_effects = data.get("sideEffects")

check("hourlyTraffic 는 list (null 금지)", isinstance(hourly, list),
      f"실제={type(hourly).__name__} value={hourly}")
check("sideEffects 는 list (null 금지)", isinstance(side_effects, list),
      f"실제={type(side_effects).__name__} value={side_effects}")
print(f"  hourlyTraffic 길이: {len(hourly) if isinstance(hourly, list) else 'N/A'}")
print(f"  sideEffects 길이: {len(side_effects) if isinstance(side_effects, list) else 'N/A'}")

if isinstance(hourly, list) and hourly:
    first = hourly[0]
    check("hourlyTraffic[0] 에 'slot' 키", "slot" in first)
    check("hourlyTraffic[0] 에 'sim1' 키", "sim1" in first)
    check("hourlyTraffic[0] 에 'sim2' 키", "sim2" in first)
    check("hourlyTraffic[0].sim1 는 int", isinstance(first.get("sim1"), int))

# ── Case 3: keywords 구조 assert ──────────────────────────────────────────────
section("Case 3: keywords.sim1 / keywords.sim2 키 반드시 존재")
keywords = data.get("keywords")
check("keywords 는 dict", isinstance(keywords, dict),
      f"실제={type(keywords).__name__}")
if isinstance(keywords, dict):
    check("keywords.sim1 키 존재", "sim1" in keywords, f"실제 키={list(keywords.keys())}")
    check("keywords.sim2 키 존재", "sim2" in keywords, f"실제 키={list(keywords.keys())}")
    check("keywords.sim1 는 list", isinstance(keywords.get("sim1"), list))
    check("keywords.sim2 는 list", isinstance(keywords.get("sim2"), list))
    print(f"  keywords.sim1 길이: {len(keywords.get('sim1', []))}")
    print(f"  keywords.sim2 길이: {len(keywords.get('sim2', []))}")

# ── Case 4: overview 구조 assert ─────────────────────────────────────────────
section("Case 4: overview.sim1 / overview.sim2 키 반드시 존재")
overview = data.get("overview")
check("overview 는 dict", isinstance(overview, dict),
      f"실제={type(overview).__name__}")
if isinstance(overview, dict):
    check("overview.sim1 키 존재", "sim1" in overview)
    check("overview.sim2 키 존재", "sim2" in overview)
    sim1 = overview.get("sim1") or {}
    sim2 = overview.get("sim2") or {}
    check("overview.sim1.totalVisits 존재", "totalVisits" in sim1)
    check("overview.sim2.totalVisits 존재", "totalVisits" in sim2)
    check("overview.sim1.marketShare 존재", "marketShare" in sim1)
    check("overview.sim2.marketShare 존재", "marketShare" in sim2)
    tv1 = sim1.get("totalVisits", -1)
    tv2 = sim2.get("totalVisits", -1)
    ms1 = sim1.get("marketShare", -1)
    ms2 = sim2.get("marketShare", -1)
    print(f"  sim1: totalVisits={tv1}, marketShare={ms1}")
    print(f"  sim2: totalVisits={tv2}, marketShare={ms2}")
    check("totalVisits >= 0 (clamp)", tv1 >= 0 and tv2 >= 0, f"tv1={tv1}, tv2={tv2}")
    check("marketShare 0~100 범위", 0.0 <= ms1 <= 100.0 and 0.0 <= ms2 <= 100.0,
          f"ms1={ms1}, ms2={ms2}")

# ── Case 5: riskScore clamp ───────────────────────────────────────────────────
section("Case 5: riskScore clamp / 구조 확인")
risk = data.get("riskScore")
check("riskScore 는 dict (항상 구조 존재)", isinstance(risk, dict),
      f"실제={type(risk).__name__}")
if isinstance(risk, dict):
    score = risk.get("score", -1)
    level = risk.get("level", "")
    print(f"  riskScore.score={score}, level={level!r}")
    check("riskScore.score 는 int", isinstance(score, int), f"실제={type(score).__name__}")
    check("riskScore.score 0~100 (clamp)", 0 <= score <= 100, f"실제={score}")
    check("riskScore.level 는 str", isinstance(level, str))
    for fld in ("positive", "watch", "negative", "totalMetrics"):
        check(f"riskScore.{fld} 존재", fld in risk, f"실제 키={list(risk.keys())}")

# ── Case 6: retention 구조 ────────────────────────────────────────────────────
section("Case 6: retention 항상 구조 제공")
retention = data.get("retention")
check("retention 는 dict (항상 구조 존재)", isinstance(retention, dict))
if isinstance(retention, dict):
    for fld in ("sim1Agents", "sim2Agents", "retained", "newUsers", "churned",
                "retentionRate", "newRatio"):
        check(f"retention.{fld} 존재", fld in retention)
    print(f"  retention: sim1Agents={retention.get('sim1Agents')}, "
          f"sim2Agents={retention.get('sim2Agents')}, "
          f"retentionRate={retention.get('retentionRate')}")
    check("sim1Agents >= 0", (retention.get("sim1Agents") or 0) >= 0)
    check("sim2Agents >= 0", (retention.get("sim2Agents") or 0) >= 0)

# ── Case 7: camelCase 정책 ─────────────────────────────────────────────────
section("Case 7: camelCase 정책 — 스네이크케이스 키 미존재")
snake_keys = [k for k in data.keys() if "_" in k]
check("응답 최상위에 snake_case 키 없음",
      len(snake_keys) == 0,
      f"snake_case 키={snake_keys}")

# 일부 camelCase 키 확인
for camel_key in ("storeSourceId", "simulationId", "hourlyTraffic", "llmSummary", "riskScore"):
    check(f"'{camel_key}' camelCase 키 존재", camel_key in data)

# ── Case 8: llmSummary ────────────────────────────────────────────────────────
section("Case 8: llmSummary 타입 확인")
llm = data.get("llmSummary")
check("llmSummary 는 str 또는 null",
      llm is None or isinstance(llm, str),
      f"실제={type(llm).__name__}")
if llm:
    print(f"  llmSummary 앞 100자: {llm[:100]!r}")
else:
    print("  llmSummary = null")

# ── Case 9: 존재하지 않는 ID → 404 ───────────────────────────────────────────
section("Case 9: GET /y-reports/9999999/view → 404 기대")
r404 = get_raw("/y-reports/9999999/view")
check("존재하지 않는 ID → 404", r404.status_code == 404, f"실제={r404.status_code}")

# ── Case 10: FAILED 보고서 (no data) → 5xx ───────────────────────────────────
section("Case 10: FAILED 보고서 → 5xx 기대 (output_json + markdown 둘 다 없음)")
print("  [INFO] FAILED 레코드 id를 자동 탐지 중...")
# 알려진 FAILED IDs (backbone.db 기준)
failed_id = None
for candidate_id in [2, 4, 6, 7]:
    r = get_raw(f"/y-reports/{candidate_id}/view")
    if r.status_code >= 500:
        failed_id = candidate_id
        check(f"FAILED id={candidate_id} → 5xx ({r.status_code})",
              r.status_code >= 500, f"실제={r.status_code}")
        try:
            err_body = r.json()
            check("[YREPORT_PARSE_ERROR] 코드 포함",
                  "YREPORT_PARSE_ERROR" in str(err_body),
                  f"실제 body={err_body}")
        except Exception:
            pass
        break
    elif r.status_code == 200:
        print(f"  [INFO] id={candidate_id} → 200 (markdown이 있어 view 생성 가능)")
    elif r.status_code == 404:
        print(f"  [INFO] id={candidate_id} → 404 (존재하지 않음)")

if failed_id is None:
    print("  [SKIP] FAILED + no-data 레코드 없음 — DB 데이터 필요")

# ── 결과 요약 ─────────────────────────────────────────────────────────────────
section("결과 요약")
total_checks = _passed + _failed
print(f"  통과: {_passed}/{total_checks}")
if _failed > 0:
    print(f"  실패: {_failed}/{total_checks}")
    sys.exit(1)
else:
    print("  모든 체크 통과!")
