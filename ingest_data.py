"""
데이터 Ingest 스크립트
원천 데이터를 API를 통해 데이터베이스에 저장합니다.
"""
import json
import requests
from pathlib import Path

def ingest_data():
    # JSON 파일 읽기
    market_file = Path("data/raw/최종_상권X매출X리뷰.json")
    population_file = Path("data/raw/최종_인구_DB.json")

    print("[INFO] JSON files loading...")
    with open(market_file, "r", encoding="utf-8") as f:
        market_data = json.load(f)
    print(f"  [OK] {market_file.name}: {len(market_data)} records")

    with open(population_file, "r", encoding="utf-8") as f:
        population_data = json.load(f)
    print(f"  [OK] {population_file.name}: {len(population_data)} records")

    # Ingest 요청 생성
    payload = {
        "snapshot_version": "v1",
        "market_sales_review": market_data,
        "population": population_data,
    }

    # API 호출
    print("\n[INFO] Starting data ingest...")
    url = "http://127.0.0.1:8000/admin/ingest"

    try:
        response = requests.post(url, json=payload, timeout=600)
        response.raise_for_status()

        result = response.json()
        print("\n[SUCCESS] Ingest completed!")
        print(f"  - Snapshot Version: {result['snapshot_version']}")
        print(f"  - Stores Inserted: {result['stores_inserted']}")
        print(f"  - Stores Updated: {result['stores_updated']}")
        print(f"  - Documents Inserted: {result['documents_inserted']}")
        print(f"  - Documents Updated: {result['documents_updated']}")

    except requests.exceptions.HTTPError as e:
        print(f"\n[ERROR] HTTP error: {e}")
        print(f"Response: {e.response.text}")
    except Exception as e:
        print(f"\n[ERROR] Exception: {e}")

if __name__ == "__main__":
    ingest_data()
