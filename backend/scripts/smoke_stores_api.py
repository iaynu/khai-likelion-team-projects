"""
GET /stores 스모크 테스트

실행 방법:
    # 서버 실행 후
    python -m scripts.smoke_stores_api

    # 또는 BASE_URL 지정
    BASE_URL=http://localhost:8000 python -m scripts.smoke_stores_api

확인 항목:
    1) GET /stores?limit=3      → items 길이, 첫 item 키 목록
    2) GET /stores?q=<샘플>     → 검색 결과 길이
    3) lat/lng null 처리 로그   → rankPercent 키 포함 여부 (camelCase 정책)
    4) pagination 필드          → limit / offset / total 포함 여부
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


def get(path: str, **params) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] 서버에 연결할 수 없음: {url}")
        print("       uvicorn app.main:app --reload 로 서버를 먼저 실행하세요.\n")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] HTTP {e.response.status_code}: {e.response.text[:200]}")
        sys.exit(1)


def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("="*60)


# ── Case 1: limit=3 기본 조회 ──────────────────────────────────────────────
section("Case 1: GET /stores?limit=3")
data = get("/stores", limit=3)

print(f"  total={data.get('total')}, limit={data.get('limit')}, offset={data.get('offset')}")
items = data.get("items", [])
print(f"  items 길이: {len(items)}")

check("응답에 'items' 키 포함", "items" in data)
check("응답에 'total' 키 포함", "total" in data)
check("응답에 'limit' 키 포함", "limit" in data)
check("응답에 'offset' 키 포함", "offset" in data)
check("limit 값이 요청한 3과 일치", data.get("limit") == 3, f"실제={data.get('limit')}")
check("offset 값이 0", data.get("offset") == 0)
check("items는 list (null 금지)", isinstance(items, list))
check("items 길이가 limit 이하", len(items) <= 3, f"실제={len(items)}")

if items:
    first = items[0]
    print(f"\n  첫 번째 item 키 목록: {sorted(first.keys())}")
    required_keys = {"id", "name", "lat", "lng", "sentiment", "revenue", "grade", "rankPercent"}
    for key in required_keys:
        check(f"  item에 '{key}' 키 포함", key in first, f"실제 키={list(first.keys())}")
    print(f"\n  첫 번째 item:\n{json.dumps(first, ensure_ascii=False, indent=4)}")

# ── Case 2: q 검색 ─────────────────────────────────────────────────────────
section("Case 2: GET /stores?q=부대&limit=5")
data2 = get("/stores", q="부대", limit=5)

items2 = data2.get("items", [])
print(f"  '부대' 검색 결과: {len(items2)}개 (total={data2.get('total')})")
check("q 검색 결과가 list", isinstance(items2, list))
check("검색 결과 limit 적용", data2.get("limit") == 5)

# ── Case 3: offset 페이지네이션 ────────────────────────────────────────────
section("Case 3: GET /stores?limit=5&offset=5 (2페이지)")
data3 = get("/stores", limit=5, offset=5)
items3 = data3.get("items", [])
print(f"  2페이지 items: {len(items3)}개")
check("offset=5 응답에 offset 값 포함", data3.get("offset") == 5, f"실제={data3.get('offset')}")

# ── Case 4: 잘못된 offset (-1) → 422 ──────────────────────────────────────
section("Case 4: GET /stores?offset=-1 → 422 기대")
url = f"{BASE_URL}/stores"
try:
    r = requests.get(url, params={"offset": -1}, timeout=TIMEOUT)
    check("offset=-1 → 422 Unprocessable Entity", r.status_code == 422, f"실제={r.status_code}")
except Exception as e:
    check("offset=-1 → 422", False, str(e))

# ── Case 5: camelCase 정책 확인 ────────────────────────────────────────────
section("Case 5: camelCase 정책 — rankPercent 키 확인")
data5 = get("/stores", limit=1)
items5 = data5.get("items", [])
if items5:
    check("rankPercent 키 존재 (camelCase)", "rankPercent" in items5[0])
    check("snake_case 키 'rank_percent' 미존재", "rank_percent" not in items5[0])
else:
    print("  [SKIP] items 없음 — DB 데이터 필요")

# ── 결과 요약 ──────────────────────────────────────────────────────────────
section("결과 요약")
total_checks = _passed + _failed
print(f"  통과: {_passed}/{total_checks}")
if _failed > 0:
    print(f"  실패: {_failed}/{total_checks}")
    sys.exit(1)
else:
    print("  모든 체크 통과!")
