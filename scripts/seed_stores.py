"""
매장 데이터 시드 스크립트 — backbone.db 업데이트

사용법:
    python scripts/seed_stores.py
    python scripts/seed_stores.py --dry-run   # 실제 반영 없이 검증만
    python scripts/seed_stores.py --db-path path/to/backbone.db

수행 작업:
    1) data/raw/split_by_store_id_ver5_/*.json
       → stores 테이블의 store_source_id 기준 매핑
       → store_documents.payload 업데이트 (MARKET_SALES_REVIEW, snapshot_id=1)
       → lat / lng 포함 (metadata.y → lat, metadata.x → lng)

    2) data/raw/최종_인구_DB.json
       → area_code 기준 POPULATION doc 업데이트 (pop_<area_code> stores)
       → 최신 분기를 primary 데이터로, 전체 분기를 history로 보존
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ── 경로 상수 ────────────────────────────────────────────────────────────────

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB   = os.path.join(BACKEND_ROOT, "backbone.db")
STORE_DIR    = os.path.join(BACKEND_ROOT, "data", "raw", "split_by_store_id_ver5_")

def _find_pop_db(raw_dir: str) -> Optional[str]:
    """data/raw 안의 인구_DB JSON 파일 경로 반환 (한글 파일명 인식)."""
    for fname in os.listdir(raw_dir):
        if fname.endswith(".json") and "DB" in fname:
            return os.path.join(raw_dir, fname)
    return None

RAW_DIR  = os.path.join(BACKEND_ROOT, "data", "raw")
POP_FILE = _find_pop_db(RAW_DIR)


# ── 페이로드 빌더 ─────────────────────────────────────────────────────────────

def build_market_payload(file_data: dict) -> dict:
    """
    split_by_store JSON → store_documents.payload 변환.

    store_service._extract_coords() 가 top-level 'lat'/'lng' 키를 탐색하므로
    metadata.y(위도) → lat, metadata.x(경도) → lng 매핑하여 상위에 추가.

    store_service._extract_sentiment() 는 analysis_reports[0].review_metrics
    를 탐색하므로 동일 데이터를 analysis_reports 래퍼로도 유지.
    """
    meta = file_data.get("metadata") or {}

    # 좌표 변환 (x=경도=lng, y=위도=lat)
    try:
        lat = float(meta.get("y") or 0) or None
        lng = float(meta.get("x") or 0) or None
    except (TypeError, ValueError):
        lat = lng = None

    # analysis_reports 래퍼 (기존 store_service 호환)
    report_entry = {
        "store_id":        file_data.get("store_id", ""),
        "store_name":      file_data.get("store_name", ""),
        "analysis_date":   file_data.get("analysis_date", ""),
        "review_metrics":  file_data.get("review_metrics"),
        "top_keywords":    file_data.get("top_keywords", []),
        "critical_feedback": file_data.get("critical_feedback", []),
        "rag_context":     file_data.get("rag_context", ""),
        # 좌표도 리포트 안에 포함 (fallback 탐색 경로)
        "lat": lat,
        "lng": lng,
    }

    payload = {
        **file_data,            # 신규 필드 전부 보존 (market_analysis, 별점, 태그 등)
        "lat": lat,             # top-level 좌표 (1순위 탐색)
        "lng": lng,
        "analysis_reports": [report_entry],  # 기존 서비스 호환 래퍼
        "report_count":     1,
    }
    return payload


def build_population_payload(records: List[dict]) -> dict:
    """
    최종_인구_DB 레코드 목록 → POPULATION doc payload.

    records: 동일 area_code의 모든 분기 레코드 (최신순)
    최신 분기를 primary 데이터로, 전체를 history 배열에 보존.
    """
    # 분기 내림차순 정렬 (20253 > 20252 > ...)
    sorted_records = sorted(
        records,
        key=lambda r: r.get("metadata", {}).get("quarter", "0"),
        reverse=True,
    )
    latest = sorted_records[0]
    return {
        **latest,
        "history": sorted_records,   # 전체 분기 이력
        "report_count": len(sorted_records),
    }


# ── DB 업데이트 ───────────────────────────────────────────────────────────────

def load_store_files(store_dir: str) -> Dict[str, dict]:
    """split_by_store 폴더 → {store_id: file_data} 딕셔너리."""
    result: Dict[str, dict] = {}
    for fname in os.listdir(store_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(store_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            sid = str(data.get("store_id", "")).strip()
            if sid:
                result[sid] = data
        except Exception as e:
            print(f"  [WARN] {fname} 읽기 실패: {e}")
    return result


def load_population_db(pop_file: str) -> Dict[str, List[dict]]:
    """최종_인구_DB.json → {area_code: [records]} 딕셔너리."""
    with open(pop_file, encoding="utf-8") as f:
        records = json.load(f)
    result: Dict[str, List[dict]] = {}
    for rec in records:
        area_code = rec.get("metadata", {}).get("area_code", "")
        if area_code:
            result.setdefault(area_code, []).append(rec)
    return result


def run_seed(db_path: str, dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*60}")
    print("  매장 데이터 시드 (backbone.db 업데이트)")
    print(f"{'='*60}")
    print(f"  DB      : {db_path}")
    print(f"  stores  : {STORE_DIR}")
    print(f"  pop_db  : {POP_FILE}")
    print(f"  dry_run : {dry_run}")
    print(f"  시각    : {now}\n")

    # ── 파일 로드 ───────────────────────────────────────────────
    print("[1/4] split_by_store 파일 로드 중 ...")
    store_files = load_store_files(STORE_DIR)
    print(f"      로드 완료: {len(store_files)}개 매장 파일")

    print("[2/4] 인구 DB 로드 중 ...")
    if POP_FILE is None:
        print("      [WARN] 인구 DB 파일 없음 — 인구 업데이트 건너뜀")
        pop_db: Dict[str, List[dict]] = {}
    else:
        pop_db = load_population_db(POP_FILE)
        print(f"      로드 완료: {sum(len(v) for v in pop_db.values())}건 "
              f"({len(pop_db)}개 area_code)")

    # ── DB 연결 ─────────────────────────────────────────────────
    print("[3/4] DB 매핑 및 업데이트 준비 ...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # store_source_id → (store_uuid, doc_id_or_None) 매핑 구축
    rows = conn.execute("""
        SELECT s.store_source_id, s.id AS store_uuid,
               sd.id AS doc_id, sd.snapshot_id
        FROM stores s
        LEFT JOIN store_documents sd
               ON sd.store_id = s.id AND sd.doc_type = 'MARKET_SALES_REVIEW'
        WHERE s.store_source_id NOT LIKE 'pop_%'
    """).fetchall()

    # snapshot_id 결정 (가장 많이 사용되는 것)
    snapshot_ids = [r["snapshot_id"] for r in rows if r["snapshot_id"]]
    primary_snapshot_id = max(set(snapshot_ids), key=snapshot_ids.count) if snapshot_ids else 1

    updated  = 0
    inserted = 0
    skipped  = 0

    updates_market: list = []   # (payload_json, doc_id)
    inserts_market: list = []   # (store_uuid, snapshot_id, payload_json)

    for row in rows:
        source_id = row["store_source_id"]
        file_data = store_files.get(source_id)
        if file_data is None:
            skipped += 1
            continue

        payload = build_market_payload(file_data)
        payload_json = json.dumps(payload, ensure_ascii=False)

        if row["doc_id"] is not None:
            updates_market.append((payload_json, row["doc_id"]))
            updated += 1
        else:
            inserts_market.append((row["store_uuid"], primary_snapshot_id, payload_json))
            inserted += 1

    print(f"      MARKET_SALES_REVIEW: 업데이트={updated}, 신규삽입={inserted}, "
          f"파일없음(건너뜀)={skipped}")

    # ── POPULATION 업데이트 ────────────────────────────────────
    pop_rows = conn.execute("""
        SELECT s.store_source_id, s.id AS store_uuid, sd.id AS doc_id, sd.snapshot_id
        FROM stores s
        LEFT JOIN store_documents sd
               ON sd.store_id = s.id AND sd.doc_type = 'POPULATION'
        WHERE s.store_source_id LIKE 'pop_%'
    """).fetchall()

    updates_pop: list = []
    inserts_pop: list = []

    for row in pop_rows:
        # pop_11440690 → area_code = 11440690
        area_code = row["store_source_id"].replace("pop_", "")
        records = pop_db.get(area_code)
        if records is None:
            print(f"      [WARN] area_code={area_code} 인구 데이터 없음 — 건너뜀")
            continue
        payload = build_population_payload(records)
        payload_json = json.dumps(payload, ensure_ascii=False)

        if row["doc_id"] is not None:
            updates_pop.append((payload_json, row["doc_id"]))
        else:
            updates_pop.append(None)  # shouldn't happen
            inserts_pop.append((row["store_uuid"], primary_snapshot_id, payload_json))

    print(f"      POPULATION: 업데이트={len([x for x in updates_pop if x])}, "
          f"신규삽입={len(inserts_pop)}")

    # ── 실제 반영 ────────────────────────────────────────────────
    print("[4/4] DB 반영 중 ..." + (" [DRY RUN - 실제 변경 없음]" if dry_run else ""))

    if not dry_run:
        try:
            with conn:  # 자동 commit/rollback
                # MARKET_SALES_REVIEW 업데이트
                if updates_market:
                    conn.executemany(
                        "UPDATE store_documents SET payload=? WHERE id=?",
                        updates_market,
                    )
                if inserts_market:
                    conn.executemany(
                        """INSERT INTO store_documents (store_id, snapshot_id, doc_type, payload, created_at)
                           VALUES (?, ?, 'MARKET_SALES_REVIEW', ?, datetime('now'))""",
                        inserts_market,
                    )
                # POPULATION 업데이트
                if updates_pop:
                    conn.executemany(
                        "UPDATE store_documents SET payload=? WHERE id=?",
                        [x for x in updates_pop if x],
                    )
                if inserts_pop:
                    conn.executemany(
                        """INSERT INTO store_documents (store_id, snapshot_id, doc_type, payload, created_at)
                           VALUES (?, ?, 'POPULATION', ?, datetime('now'))""",
                        inserts_pop,
                    )
            print(f"  완료: MARKET {updated+inserted}건, POPULATION {len(updates_pop)+len(inserts_pop)}건 반영")
        except Exception as e:
            print(f"  [ERROR] DB 업데이트 실패: {e}")
            conn.close()
            sys.exit(1)
    else:
        print(f"  [DRY RUN] MARKET {updated+inserted}건, POPULATION {len(updates_pop)+len(inserts_pop)}건 예정")

    conn.close()

    # ── 결과 검증 ────────────────────────────────────────────────
    if not dry_run:
        print("\n검증 중 ...")
        conn2 = sqlite3.connect(db_path)
        cnt = conn2.execute(
            "SELECT count(*) FROM store_documents WHERE doc_type='MARKET_SALES_REVIEW'"
        ).fetchone()[0]
        pop_cnt = conn2.execute(
            "SELECT count(*) FROM store_documents WHERE doc_type='POPULATION'"
        ).fetchone()[0]

        # 좌표 추출 가능 건수 확인
        sample_rows = conn2.execute(
            "SELECT payload FROM store_documents WHERE doc_type='MARKET_SALES_REVIEW' LIMIT 5"
        ).fetchall()
        with_coords = sum(
            1 for r in sample_rows
            if json.loads(r[0]).get("lat") is not None
        )

        conn2.close()
        print(f"  MARKET_SALES_REVIEW docs : {cnt}")
        print(f"  POPULATION docs          : {pop_cnt}")
        print(f"  좌표 포함(샘플5) : {with_coords}/5")
        print("\n  ALL DONE")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="매장 데이터 DB 시드")
    parser.add_argument("--db-path", default=DEFAULT_DB,
                        help=f"backbone.db 경로 (기본: {DEFAULT_DB})")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 반영 없이 검증만 수행")
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"[ERROR] DB 파일 없음: {args.db_path}")
        sys.exit(1)
    if not os.path.isdir(STORE_DIR):
        print(f"[ERROR] 매장 데이터 폴더 없음: {STORE_DIR}")
        sys.exit(1)

    run_seed(db_path=args.db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
