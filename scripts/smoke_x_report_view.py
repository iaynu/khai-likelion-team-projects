"""
GET /x-reports/{id}/view 스모크 테스트

실행 방법:
    # 서버 실행 후
    python -m scripts.smoke_x_report_view

    # 특정 report_id 지정
    XREPORT_ID=1 python -m scripts.smoke_x_report_view

    # BASE_URL 지정
    BASE_URL=http://localhost:8000 python -m scripts.smoke_x_report_view

확인 항목:
    1) GET /x-reports/{id}/view  → 200 + 필수 키 존재 여부
    2) radarData — 5개 축 + subject/A/fullMark 키 포함
    3) keywords  — list (null 금지)
    4) solutions — list (null 금지), 각 항목 키 확인
    5) fullReport — str | null
    6) GET /x-reports/9999999/view → 404 기대
"""
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
            print(f"\n[ERROR] HTTP {r.status_code} (expected {expected_status}): {r.text[:200]}")
            sys.exit(1)
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] 서버에 연결할 수 없음: {url}")
        print("       uvicorn app.main:app --reload 로 서버를 먼저 실행하세요.\n")
        sys.exit(1)


def get_raw(path: str) -> requests.Response:
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


# ── report_id 결정 ─────────────────────────────────────────────────────────
REPORT_ID = int(os.getenv("XREPORT_ID", "1"))

# ── Case 1: 기본 조회 ──────────────────────────────────────────────────────
section(f"Case 1: GET /x-reports/{REPORT_ID}/view")
data = get(f"/x-reports/{REPORT_ID}/view")

print(f"  응답 키 목록: {sorted(data.keys())}")

required_top_keys = {"id", "storeId", "status", "radarData", "keywords", "solutions"}
for key in required_top_keys:
    check(f"응답에 '{key}' 키 포함", key in data, f"실제 키={list(data.keys())}")

check("id 가 int", isinstance(data.get("id"), int), f"실제={data.get('id')}")
check("storeId 가 str", isinstance(data.get("storeId"), str), f"실제={data.get('storeId')}")
check("status 가 str", isinstance(data.get("status"), str))

# ── Case 2: radarData 검증 ──────────────────────────────────────────────────
section("Case 2: radarData 구조")
radar = data.get("radarData", [])
print(f"  radarData 길이: {len(radar)}")
check("radarData 는 list", isinstance(radar, list))
check("radarData 는 null 아님", radar is not None)

EXPECTED_SUBJECTS = {"맛", "서비스", "분위기", "가격", "위생"}
if radar:
    first_radar = radar[0]
    print(f"  첫 번째 radarData 항목: {first_radar}")
    check("radarData[0] 에 'subject' 키", "subject" in first_radar)
    check("radarData[0] 에 'A' 키", "A" in first_radar)
    check("radarData[0] 에 'fullMark' 키", "fullMark" in first_radar)
    check("A 값이 int", isinstance(first_radar.get("A"), int))
    check("fullMark 값이 int", isinstance(first_radar.get("fullMark"), int))

    subjects_in_response = {item.get("subject") for item in radar if isinstance(item, dict)}
    check(
        f"radarData 에 예상 subject 포함 ({EXPECTED_SUBJECTS})",
        EXPECTED_SUBJECTS.issubset(subjects_in_response),
        f"실제 subjects={subjects_in_response}",
    )
else:
    print("  [SKIP] radarData 없음 — 플레이스홀더 확인 불가")

# ── Case 3: keywords 검증 ───────────────────────────────────────────────────
section("Case 3: keywords 구조")
keywords = data.get("keywords", [])
print(f"  keywords 길이: {len(keywords)}")
check("keywords 는 list (null 금지)", isinstance(keywords, list))

if keywords:
    first_kw = keywords[0]
    print(f"  첫 번째 keyword: {first_kw}")
    check("keyword 에 'text' 키", "text" in first_kw)
    check("keyword 에 'sentiment' 키", "sentiment" in first_kw)
    check(
        "sentiment 값이 유효",
        first_kw.get("sentiment") in ("neutral", "positive", "negative"),
        f"실제={first_kw.get('sentiment')}",
    )
else:
    print("  [SKIP] keywords 없음 — DB 데이터 필요")

# ── Case 4: solutions 검증 ──────────────────────────────────────────────────
section("Case 4: solutions 구조")
solutions = data.get("solutions", [])
print(f"  solutions 길이: {len(solutions)}")
check("solutions 는 list (null 금지)", isinstance(solutions, list))
check("solutions 길이 ≤ 9", len(solutions) <= 9, f"실제={len(solutions)}")

if solutions:
    first_sol = solutions[0]
    print(f"  첫 번째 solution:\n{json.dumps(first_sol, ensure_ascii=False, indent=4)}")
    for key in ("category", "title", "desc", "execution", "effect"):
        check(f"solution 에 '{key}' 키", key in first_sol, f"실제 키={list(first_sol.keys())}")
else:
    print("  [SKIP] solutions 없음 — DB / config_json 데이터 필요")

# ── Case 5: fullReport ──────────────────────────────────────────────────────
section("Case 5: fullReport 필드")
full_report = data.get("fullReport")
check("fullReport 키 존재", "fullReport" in data)
if full_report is not None:
    check("fullReport 는 str", isinstance(full_report, str))
    print(f"  fullReport 앞 100자: {full_report[:100]!r}")
else:
    print("  [INFO] fullReport = null (content_text 없음)")

# ── Case 6: 존재하지 않는 report_id → 404 ──────────────────────────────────
section("Case 6: GET /x-reports/9999999/view → 404 기대")
r404 = get_raw("/x-reports/9999999/view")
check("존재하지 않는 ID → 404", r404.status_code == 404, f"실제={r404.status_code}")

# ── 결과 요약 ───────────────────────────────────────────────────────────────
section("결과 요약")
total_checks = _passed + _failed
print(f"  통과: {_passed}/{total_checks}")
if _failed > 0:
    print(f"  실패: {_failed}/{total_checks}")
    sys.exit(1)
else:
    print("  모든 체크 통과!")
