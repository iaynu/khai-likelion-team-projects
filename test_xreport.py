"""
X-Report 생성 테스트
첫 번째 매장의 X-Report를 생성하고 12개 전략을 확인합니다.
"""
import requests
import json

def test_xreport():
    base_url = "http://127.0.0.1:8000"

    # 1. 첫 번째 매장 가져오기
    print("[1] Getting first store...")
    response = requests.get(f"{base_url}/stores?limit=1")
    response.raise_for_status()
    stores = response.json()

    if stores["total"] == 0:
        print("[ERROR] No stores found!")
        return

    first_store = stores["items"][0]
    store_id = first_store["id"]
    store_source_id = first_store["store_source_id"]

    print(f"  [OK] Store ID: {store_id}")
    print(f"  [OK] Store Source ID: {store_source_id}")

    # 2. X-Report 생성
    print("\n[2] Creating X-Report...")
    payload = {
        "store_source_id": store_source_id,
        "snapshot_version": "v1",
        "prompt_id": "default_prompt",
        "force_regenerate": False
    }

    response = requests.post(f"{base_url}/x-reports", json=payload)
    response.raise_for_status()
    report = response.json()

    report_id = report["id"]
    print(f"  [OK] Report ID: {report_id}")
    print(f"  [OK] Content length: {len(report.get('content_text', ''))} chars")

    # 3. Strategies 조회
    print("\n[3] Getting strategies...")
    response = requests.get(f"{base_url}/x-reports/{report_id}/strategies")
    response.raise_for_status()
    strategies = response.json()

    print(f"  [OK] Total strategies: {strategies['total']}")

    # 4. Strategy 상세 출력
    print("\n[4] Strategy details:")
    for idx, strat in enumerate(strategies["items"], 1):
        print(f"  #{idx} - Group {strat['solution_group']}, Action {strat['action_idx']}: {strat['title']}")

    print("\n[SUCCESS] Full flow test completed!")
    print(f"  - Store: {store_source_id}")
    print(f"  - Report: {report_id}")
    print(f"  - Strategies: {strategies['total']}")

if __name__ == "__main__":
    try:
        test_xreport()
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
