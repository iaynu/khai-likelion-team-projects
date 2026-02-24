"""
통합 스모크 스크립트 — 프론트 계약(Contract) E2E 검증

실행 방법:
    python scripts/smoke_contract_all.py --base-url http://localhost:8000 --x-id <XREPORT_ID> --y-id <YREPORT_ID>
    python scripts/smoke_contract_all.py --x-id 1 --y-id 5 --stores-limit 3 --strict-keys --verbose

서버를 먼저 실행하세요:
    uvicorn app.main:app --reload

확인 항목:
    1) GET /stores                — camelCase 감지, 리스트/타입/pagination 검사
    2) GET /x-reports/{id}/view  — radarData/keywords/solutions 구조·타입 검사
    3) GET /y-reports/{id}/view  — overview/retention/riskScore/hourlyTraffic 구조·타입·clamp 검사
"""

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("[ERROR] requests 미설치: pip install requests")
    sys.exit(1)

# ── 출력 상수 ──────────────────────────────────────────────────────────────────

PASS_LBL = "\033[32mPASS\033[0m"
FAIL_LBL = "\033[31mFAIL\033[0m"
WARN_LBL = "\033[33mWARN\033[0m"

_DUMP_MAX = 4000   # pretty-print 최대 글자수

# ── 전역 상태 ──────────────────────────────────────────────────────────────────

_passed: int = 0
_failed: int = 0
_verbose: bool = False
_detected_case: str = "unknown"   # "camel" | "snake" | "unknown"

# ── 키 변환 헬퍼 ───────────────────────────────────────────────────────────────

def _to_snake(name: str) -> str:
    """camelCase → snake_case 변환."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def _key(camel_name: str) -> str:
    """정규 camelCase 이름을 감지된 케이스 기준으로 변환."""
    return _to_snake(camel_name) if _detected_case == "snake" else camel_name


def _get(obj: dict, camel_name: str) -> Any:
    """camelCase 또는 snake_case로 값을 조회 (둘 다 시도)."""
    v = obj.get(_key(camel_name))
    if v is None and camel_name not in obj:
        v = obj.get(_to_snake(camel_name))
    return v


# ── 케이스 감지 ────────────────────────────────────────────────────────────────

def detect_case_from_store_item(item: dict) -> str:
    """첫 Store 아이템 키에서 naming convention을 추측한다."""
    if "rankPercent" in item:
        return "camel"
    if "rank_percent" in item:
        return "snake"
    camel_hints = {"storeSourceId", "storeName", "areaCode"}
    snake_hints = {"store_source_id", "store_name", "area_code"}
    keys = set(item.keys())
    if keys & camel_hints:
        return "camel"
    if keys & snake_hints:
        return "snake"
    return "unknown"


# ── HTTP 헬퍼 ──────────────────────────────────────────────────────────────────

_TIMEOUT = 5


def request_json(url: str) -> Tuple[int, Optional[dict]]:
    """GET 요청 → (status_code, json_body|None). 연결 실패 시 exit."""
    try:
        r = requests.get(url, timeout=_TIMEOUT)
        try:
            body = r.json()
        except Exception:
            body = None
        return r.status_code, body
    except requests.exceptions.ConnectionError:
        print(f"\n[ERROR] 서버 연결 실패: {url}")
        print("       uvicorn app.main:app --reload 로 서버를 먼저 실행하세요.\n")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"\n[ERROR] 요청 타임아웃 ({_TIMEOUT}s): {url}")
        sys.exit(1)


# ── 출력 헬퍼 ──────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print(f"\n{'='*64}")
    print(f"  {title}")
    print("=" * 64)


def _truncate(s: str, max_len: int = _DUMP_MAX) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n... (truncated, total={len(s)} chars)"


def _dump_full(obj: Any) -> None:
    """--verbose 시 전체 JSON pretty-print."""
    print(_truncate(json.dumps(obj, ensure_ascii=False, indent=2)))


def _dump_partial(obj: dict, camel_keys: List[str]) -> None:
    """실패 시 관련 키 부분만 출력."""
    partial: dict = {}
    for ck in camel_keys:
        for k in (_key(ck), _to_snake(ck), ck):
            if k in obj:
                partial[k] = obj[k]
                break
    snippet = json.dumps(partial, ensure_ascii=False, indent=2)
    print(f"  [부분 응답 dump]\n{_truncate(snippet, 1500)}")


# ── 검증 헬퍼 ──────────────────────────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  {PASS_LBL}  {label}")
        return True
    _failed += 1
    print(f"  {FAIL_LBL}  {label}" + (f"  → {detail}" if detail else ""))
    return False


def warn(msg: str, detail: str = "") -> None:
    print(f"  {WARN_LBL}  {msg}" + (f"  → {detail}" if detail else ""))


def check_is_list(value: Any, context: str) -> bool:
    return check(f"{context} is list (null 금지)",
                 isinstance(value, list),
                 f"실제={type(value).__name__}")


def check_type(value: Any, types: tuple, context: str) -> bool:
    names = "|".join(t.__name__ for t in types)
    return check(f"{context} is {names}",
                 isinstance(value, types),
                 f"실제={type(value).__name__} 값={repr(value)[:80]}")


def check_range(value: Any, lo: float, hi: float, context: str) -> bool:
    try:
        v = float(value)
        return check(f"{context} {lo}~{hi} clamp",
                     lo <= v <= hi, f"실제={v}")
    except (TypeError, ValueError):
        return check(f"{context} 숫자 아님", False, f"실제={type(value).__name__}")


def check_min_keys(obj: Any, min_camel_keys: List[str], context: str) -> bool:
    """dict가 최소 키를 갖는지 확인 (빈 {} 방지)."""
    if not isinstance(obj, dict):
        return check(f"{context} is dict", False, f"실제={type(obj).__name__}")
    ok = True
    for ck in min_camel_keys:
        actual = _key(ck)
        present = (actual in obj) or (_to_snake(ck) in obj) or (ck in obj)
        ok = check(f"{context}.{actual} 존재", present,
                   f"실제 키={list(obj.keys())}") and ok
    return ok


# ── 섹션별 실패 추적 ───────────────────────────────────────────────────────────

_section_fail_mark: int = 0


def _mark_section() -> None:
    global _section_fail_mark
    _section_fail_mark = _failed


def _section_had_failures() -> bool:
    return _failed > _section_fail_mark


# ── 시나리오 1: GET /stores ────────────────────────────────────────────────────

def check_stores(base_url: str, limit: int, q: Optional[str], strict: bool) -> None:
    global _detected_case

    url = f"{base_url}/stores?limit={limit}"
    if q:
        url += f"&q={q}"
    _section(f"(1) GET /stores?limit={limit}" + (f"&q={q}" if q else ""))
    _mark_section()

    status, body = request_json(url)
    if not check("HTTP 200", status == 200, f"실제={status}"):
        if body and _verbose:
            _dump_full(body)
        return

    # ── 최상위 페이지네이션 키 ─────────────────────────────────
    for k in ("items", "total", "limit", "offset"):
        check(f"응답에 '{k}' 키 존재", k in body,
              f"실제 키={list(body.keys())}")

    items = body.get("items")
    total = body.get("total")
    resp_limit = body.get("limit")
    offset = body.get("offset")

    check_is_list(items, "items")
    check_type(total, (int,), "total")
    check_type(resp_limit, (int,), "limit")
    check_type(offset, (int,), "offset")

    # limit clamp: 응답 limit ≤ max(요청, 200)
    if isinstance(resp_limit, int):
        check("응답 limit ≤ max(요청, 200)",
              resp_limit <= max(limit, 200),
              f"resp={resp_limit} 요청={limit}")

    # ── 케이스 감지 ─────────────────────────────────────────────
    if isinstance(items, list) and items:
        first = items[0]
        _detected_case = detect_case_from_store_item(first)
    else:
        _detected_case = "camel"   # 빈 목록이면 camel 가정

    print(f"\n  Detected naming: {_detected_case}")

    if _detected_case == "unknown":
        if strict:
            check("키 네이밍 감지 성공", False,
                  f"unknown — 실제 키: {list(items[0].keys()) if items else []}")
        else:
            warn("키 네이밍 감지 실패 — non-strict 모드로 핵심 키만 검사")

    # ── Item 필드 검사 ───────────────────────────────────────────
    if isinstance(items, list) and items:
        first = items[0]
        rank_key = _key("rankPercent")

        # 필수 키
        for k in ("id", "name"):
            check(f"item.'{k}' 키 존재", k in first,
                  f"실제 키={list(first.keys())}")

        # 선택 키 (strict 시 필수)
        optional_keys = ["address", "lat", "lng", "sentiment", "revenue", "grade", rank_key]
        for k in optional_keys:
            present = k in first
            if strict:
                check(f"item.'{k}' 키 존재", present,
                      f"실제 키={list(first.keys())}")
            elif not present:
                warn(f"item.'{k}' 없음 (optional)")

        # 타입 검사
        if "id" in first:
            check_type(first["id"], (str,), "item.id")
        if "name" in first:
            check_type(first["name"], (str,), "item.name")
        for coord in ("lat", "lng"):
            if coord in first:
                v = first[coord]
                check(f"item.{coord} is float|None",
                      v is None or isinstance(v, (int, float)),
                      f"실제={type(v).__name__}")
        if "sentiment" in first:
            v = first["sentiment"]
            check("item.sentiment is float|None",
                  v is None or isinstance(v, (int, float)),
                  f"실제={type(v).__name__}")
        if rank_key in first:
            v = first[rank_key]
            check(f"item.{rank_key} is int|float|None",
                  v is None or isinstance(v, (int, float)),
                  f"실제={type(v).__name__}")

        # camelCase 정책: snake_case 키 미존재 확인
        if _detected_case == "camel":
            snake_keys = [k for k in first.keys() if "_" in k]
            check("item 최상위 snake_case 키 없음 (camelCase 정책)",
                  len(snake_keys) == 0,
                  f"snake_case 키={snake_keys}")

    print(f"  [INFO] total={total}, limit={resp_limit}, offset={offset}, "
          f"items 수={len(items) if isinstance(items, list) else 'N/A'}")

    if _verbose:
        _dump_full(body)
    elif _section_had_failures() and body:
        _dump_partial(body, ["items", "total", "limit", "offset"])


# ── 시나리오 2: GET /x-reports/{id}/view ─────────────────────────────────────

def check_x_report_view(base_url: str, x_id: int, strict: bool) -> None:
    url = f"{base_url}/x-reports/{x_id}/view"
    _section(f"(2) GET /x-reports/{x_id}/view")
    _mark_section()

    status, body = request_json(url)
    if not check("HTTP 200", status == 200, f"실제={status}"):
        if body:
            _dump_partial(body, ["detail", "code", "message"])
            if _verbose:
                _dump_full(body)
        return

    radar_key     = _key("radarData")
    keywords_key  = _key("keywords")
    solutions_key = _key("solutions")
    full_rpt_key  = _key("fullReport")

    # ── 최상위 키 존재 ─────────────────────────────────────────
    for k in (radar_key, keywords_key, solutions_key):
        check(f"응답에 '{k}' 키 존재", k in body,
              f"실제 키={list(body.keys())[:14]}")

    if strict:
        for k in (full_rpt_key, _key("storeId"), _key("status"), "id"):
            check(f"응답에 '{k}' 키 존재 (strict)", k in body,
                  f"실제 키={list(body.keys())}")

    # camelCase 정책
    if _detected_case == "camel":
        snake_keys = [k for k in body.keys() if "_" in k]
        check("응답 최상위 snake_case 키 없음 (camelCase 정책)",
              len(snake_keys) == 0,
              f"snake_case 키={snake_keys}")

    # ── radarData ──────────────────────────────────────────────
    radar = _get(body, "radarData")
    check_is_list(radar, f"'{radar_key}'")
    _RADAR_SUBJECTS = {"맛", "서비스", "분위기", "가격", "위생"}
    if isinstance(radar, list) and radar:
        r0 = radar[0]
        check_type(r0.get("subject"), (str,), f"{radar_key}[0].subject")
        check(f"{radar_key}[0].A is int|float",
              isinstance(r0.get("A"), (int, float)),
              f"실제={type(r0.get('A')).__name__}")
        check_type(r0.get("fullMark"), (int,), f"{radar_key}[0].fullMark")
        if strict:
            check(f"{radar_key}[0].reason is str|None",
                  r0.get("reason") is None or isinstance(r0.get("reason"), str))
        subjects = {r.get("subject") for r in radar if isinstance(r, dict)}
        overlap = _RADAR_SUBJECTS & subjects
        print(f"  [INFO] {radar_key} 주제: {sorted(subjects)}")
        print(f"  [INFO] 기대 5개(맛/서비스/분위기/가격/위생) 교집합: {sorted(overlap)}")
    else:
        print(f"  [INFO] {radar_key} 데이터 없음 (빈 리스트)")

    # ── keywords ───────────────────────────────────────────────
    keywords = _get(body, "keywords")
    check_is_list(keywords, f"'{keywords_key}'")
    if isinstance(keywords, list) and keywords:
        k0 = keywords[0]
        check_type(k0.get("text"), (str,), f"{keywords_key}[0].text")
        check_type(k0.get("sentiment"), (str,), f"{keywords_key}[0].sentiment")

    # ── solutions ──────────────────────────────────────────────
    solutions = _get(body, "solutions")
    check_is_list(solutions, f"'{solutions_key}'")
    if isinstance(solutions, list):
        check(f"{solutions_key} 길이 ≤ 9",
              len(solutions) <= 9, f"실제={len(solutions)}")
        if solutions:
            s0 = solutions[0]
            for field in ("category", "title", "desc", "execution", "effect"):
                v = s0.get(field)
                check(f"{solutions_key}[0].{field} is str",
                      isinstance(v, str),
                      f"실제={type(v).__name__} 값={repr(v)[:40]}")

    # ── fullReport ─────────────────────────────────────────────
    full_report = _get(body, "fullReport")
    check(f"'{full_rpt_key}' is str|None",
          full_report is None or isinstance(full_report, str),
          f"실제={type(full_report).__name__}")

    print(f"  [INFO] solutions 수={len(solutions) if isinstance(solutions, list) else 'N/A'}, "
          f"keywords 수={len(keywords) if isinstance(keywords, list) else 'N/A'}")

    if _verbose:
        _dump_full(body)
    elif _section_had_failures():
        _dump_partial(body, ["radarData", "keywords", "solutions"])


# ── 시나리오 3: GET /y-reports/{id}/view ─────────────────────────────────────

def check_y_report_view(base_url: str, y_id: int, strict: bool) -> None:
    url = f"{base_url}/y-reports/{y_id}/view"
    _section(f"(3) GET /y-reports/{y_id}/view")
    _mark_section()

    status, body = request_json(url)
    if not check("HTTP 200", status == 200, f"실제={status}"):
        if body:
            _dump_partial(body, ["detail", "code", "message"])
            if _verbose:
                _dump_full(body)
        return

    # ── 최상위 필수 키 존재 ─────────────────────────────────────
    required_camel = [
        "overview", "keywords", "ratingDistribution", "hourlyTraffic",
        "retention", "sideEffects", "riskScore", "llmSummary",
    ]
    for ck in required_camel:
        k = _key(ck)
        check(f"응답에 '{k}' 키 존재", k in body,
              f"실제 키={list(body.keys())[:16]}")

    # camelCase 정책
    if _detected_case == "camel":
        snake_keys = [k for k in body.keys() if "_" in k]
        check("응답 최상위 snake_case 키 없음 (camelCase 정책)",
              len(snake_keys) == 0,
              f"snake_case 키={snake_keys}")

    # ── hourlyTraffic: list (null 금지) ────────────────────────
    hourly = _get(body, "hourlyTraffic")
    check_is_list(hourly, f"'{_key('hourlyTraffic')}'")
    if isinstance(hourly, list) and hourly:
        h0 = hourly[0]
        check_type(h0.get("slot"), (str,), f"hourlyTraffic[0].slot")
        check(f"hourlyTraffic[0].sim1 is int",
              isinstance(h0.get("sim1"), int),
              f"실제={type(h0.get('sim1')).__name__}")
        check(f"hourlyTraffic[0].sim2 is int",
              isinstance(h0.get("sim2"), int),
              f"실제={type(h0.get('sim2')).__name__}")
        print(f"  [INFO] hourlyTraffic 길이={len(hourly)}, "
              f"첫 slot={h0.get('slot')!r}")

    # ── sideEffects: list (null 금지) ─────────────────────────
    side = _get(body, "sideEffects")
    check_is_list(side, f"'{_key('sideEffects')}'")
    print(f"  [INFO] sideEffects 길이={len(side) if isinstance(side, list) else 'N/A'}")

    # ── llmSummary: str|None ─────────────────────────────────────
    llm = _get(body, "llmSummary")
    check(f"'{_key('llmSummary')}' is str|None",
          llm is None or isinstance(llm, str),
          f"실제={type(llm).__name__}")
    print(f"  [INFO] llmSummary={'null' if llm is None else repr(llm[:60])}")

    # ── overview: dict with sim1/sim2 (empty obj {} 금지) ──────
    overview = _get(body, "overview")
    if check(f"'{_key('overview')}' is dict",
             isinstance(overview, dict),
             f"실제={type(overview).__name__}"):
        check_min_keys(overview, ["sim1", "sim2"], _key("overview"))
        sim1 = overview.get("sim1") or {}
        sim2 = overview.get("sim2") or {}
        if isinstance(sim1, dict):
            tv1 = sim1.get("totalVisits")
            ms1 = sim1.get("marketShare")
            check_type(tv1, (int,), "overview.sim1.totalVisits")
            check(f"overview.sim1.marketShare is float|int",
                  isinstance(ms1, (int, float)),
                  f"실제={type(ms1).__name__}")
            if ms1 is not None:
                check_range(ms1, 0.0, 100.0, "overview.sim1.marketShare")
        if isinstance(sim2, dict):
            tv2 = sim2.get("totalVisits")
            ms2 = sim2.get("marketShare")
            check_type(tv2, (int,), "overview.sim2.totalVisits")
            if ms2 is not None:
                check_range(ms2, 0.0, 100.0, "overview.sim2.marketShare")
        print(f"  [INFO] overview: sim1.totalVisits={sim1.get('totalVisits')}, "
              f"sim2.totalVisits={sim2.get('totalVisits')}, "
              f"sim1.marketShare={sim1.get('marketShare')}")

    # ── keywords: dict with sim1/sim2 (empty obj {} 금지) ──────
    keywords = _get(body, "keywords")
    if check(f"'{_key('keywords')}' is dict",
             isinstance(keywords, dict),
             f"실제={type(keywords).__name__}"):
        check_min_keys(keywords, ["sim1", "sim2"], _key("keywords"))
        check_is_list(keywords.get("sim1"), "keywords.sim1")
        check_is_list(keywords.get("sim2"), "keywords.sim2")

    # ── riskScore: dict with score/level (empty obj {} 금지) ───
    risk = _get(body, "riskScore")
    if check(f"'{_key('riskScore')}' is dict",
             isinstance(risk, dict),
             f"실제={type(risk).__name__}"):
        check_min_keys(risk, ["score", "level"], _key("riskScore"))
        score = risk.get("score")
        level = risk.get("level")
        check_type(score, (int,), "riskScore.score")
        check_type(level, (str,), "riskScore.level")
        if isinstance(score, int):
            check_range(score, 0, 100, "riskScore.score")
        if strict:
            for f in ("positive", "watch", "negative", "totalMetrics"):
                check(f"riskScore.{f} 존재", f in risk,
                      f"실제 키={list(risk.keys())}")
        print(f"  [INFO] riskScore: score={score}, level={level!r}")

    # ── retention: dict with sim1Agents/sim2Agents (empty obj {} 금지) ──
    retention = _get(body, "retention")
    if check(f"'{_key('retention')}' is dict",
             isinstance(retention, dict),
             f"실제={type(retention).__name__}"):
        check_min_keys(retention, ["sim1Agents", "sim2Agents"], _key("retention"))
        if strict:
            for ck in ("sim1Agents", "sim2Agents", "retained", "newUsers",
                       "churned", "retentionRate", "newRatio"):
                k = _key(ck)
                check(f"retention.{k} 존재", k in retention or ck in retention,
                      f"실제 키={list(retention.keys())}")
        s1a = _get(retention, "sim1Agents") if isinstance(retention, dict) else None
        s2a = _get(retention, "sim2Agents") if isinstance(retention, dict) else None
        rr  = _get(retention, "retentionRate") if isinstance(retention, dict) else None
        print(f"  [INFO] retention: sim1Agents={s1a}, sim2Agents={s2a}, "
              f"retentionRate={rr}")

    # ── ratingDistribution: dict|None (빈 dict 허용, 값 있으면 내부 구조 확인) ──
    rating_dist = _get(body, "ratingDistribution")
    check(f"'{_key('ratingDistribution')}' is dict|None",
          rating_dist is None or isinstance(rating_dist, dict),
          f"실제={type(rating_dist).__name__}")
    if isinstance(rating_dist, dict) and rating_dist:
        for sub_k, sub_v in list(rating_dist.items())[:3]:
            check(f"ratingDistribution.{sub_k} is dict",
                  isinstance(sub_v, dict),
                  f"실제={type(sub_v).__name__}")

    if _verbose:
        _dump_full(body)
    elif _section_had_failures():
        _dump_partial(body, [
            "overview", "keywords", "riskScore", "retention",
            "hourlyTraffic", "sideEffects", "ratingDistribution",
        ])


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    global _verbose, _detected_case

    parser = argparse.ArgumentParser(
        description="프론트 Contract 통합 스모크 테스트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="API 서버 base URL (기본: http://localhost:8000)")
    parser.add_argument("--stores-limit", type=int, default=3,
                        help="GET /stores limit 파라미터 (기본: 3)")
    parser.add_argument("--q", default=None,
                        help="GET /stores 검색어 (선택)")
    parser.add_argument("--x-id", type=int, required=True,
                        help="조회할 XReport id")
    parser.add_argument("--y-id", type=int, required=True,
                        help="조회할 YReport id")
    parser.add_argument("--strict-keys", action="store_true",
                        help="계약서 전체 키를 엄격하게 검사 (없으면 즉시 FAIL)")
    parser.add_argument("--verbose", action="store_true",
                        help="실패 시 전체 JSON dump 출력 (기본: 부분 dump)")
    args = parser.parse_args()

    _verbose = args.verbose
    base_url = args.base_url.rstrip("/")
    strict   = args.strict_keys

    print(f"\n{'='*64}")
    print("  프론트 Contract 통합 스모크 테스트 (BACKEND-4)")
    print(f"{'='*64}")
    print(f"  base_url    : {base_url}")
    print(f"  x_id        : {args.x_id}")
    print(f"  y_id        : {args.y_id}")
    print(f"  stores_limit: {args.stores_limit}")
    print(f"  strict_keys : {strict}")
    print(f"  verbose     : {_verbose}")

    check_stores(base_url, args.stores_limit, args.q, strict)
    check_x_report_view(base_url, args.x_id, strict)
    check_y_report_view(base_url, args.y_id, strict)

    # ── 최종 결과 ──────────────────────────────────────────────
    _section("결과 요약")
    total = _passed + _failed
    print(f"  통과: {_passed}/{total}")
    if _failed > 0:
        print(f"  실패: {_failed}/{total}")
        print(f"\n  CHECKS FAILED ({_failed})")
        sys.exit(1)
    else:
        print("\n  ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
