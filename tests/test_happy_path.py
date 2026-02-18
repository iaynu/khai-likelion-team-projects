"""
Happy-path integration test:
  1) POST /admin/ingest   → inserts stores + documents
  2) POST /x-reports      → generates X-Report + 12 strategies
  3) GET  /x-reports/{id}/strategies → verifies 12 strategies (3 groups × 4 actions)
"""
import pytest


# ── Sample payloads (minimal valid data) ──────────────────
MARKET_SALES_REVIEW_PAYLOAD = [
    {
        "id": "test_area_section_sector_20253",
        "market_analysis": {
            "phase": "성장",
            "trend_review": "테스트 트렌드",
            "competition_intensity": "낮음",
        },
        "revenue_analysis": "테스트 매출 분석",
        "customer_analysis": "테스트 고객 분석",
        "review_metrics": None,
        "raw_data_context": {
            "trend_history": "[20253] 점포1개",
            "metrics": {
                "store_count": 1,
                "net_change": 0,
                "close_rate": 0.0,
                "has_sales_data": False,
            },
        },
        "metadata": {
            "area": "테스트동",
            "section": "테스트구역",
            "sector": "한식음식점",
            "quarter": "20253",
        },
        "analysis_reports": [
            {
                "store_id": "9999001",
                "store_name": "테스트한식집",
                "analysis_date": "2026-02-05",
                "review_metrics": {
                    "overall_sentiment": {"score": 0.8, "label": "긍정적", "comparison": "좋음"},
                    "feature_scores": {
                        "taste": {"score": 0.85, "label": "매우 좋음", "avg_score": 0.78},
                        "price_value": {"score": 0.7, "label": "좋음", "avg_score": 0.6},
                        "cleanliness": {"score": 0.9, "label": "매우 좋음", "avg_score": 0.75},
                        "service": {"score": 0.75, "label": "좋음", "avg_score": 0.7},
                    },
                },
                "top_keywords": ["맛있음", "가성비"],
                "critical_feedback": ["주문 속도 개선 필요"],
                "rag_context": "테스트 rag 컨텍스트 문자열",
            }
        ],
        "report_count": 1,
    }
]

POPULATION_PAYLOAD = [
    {
        "id": "11440690_20253_population",
        "population_analysis": {
            "overall_summary": "거주인구 18,276명 / 유동인구 6,696,623명",
            "gender_structure": "남성 44%, 여성 56%",
            "age_structure": "30대 상위",
            "temporal_pattern": "00-06 상위",
            "working_population_summary": "직장인구 3,418명",
        },
        "dimension_analysis": {
            "by_time": "00-06(28.7%)",
            "by_weekday": "일(15.6%)",
            "by_age": "30대(22.1%)",
        },
        "raw_data_context": {
            "source_snapshots": [],
            "metrics": {
                "reside_total": 18276.0,
                "floating_total": 6696623.0,
                "processed_time_total_sum": None,
                "weekend_weekday_ratio_floating": 1.13,
                "working_total": 3418.0,
                "working_male": 1953.0,
                "working_female": 1465.0,
                "working_to_floating_ratio": 0.0005,
            },
            "dimension_columns": {"processed": [], "floating": ["by_weekday", "by_time", "by_age"]},
            "dimension_breakdowns": {},
        },
        "metadata": {
            "area_code": "11440690",
            "area_name": "망원1동",
            "quarter": "20253",
            "data_type": "인구(거주/유동/생활) 통합",
        },
        "korean_field_description": {},
    }
]


@pytest.mark.asyncio
async def test_happy_path_ingest_xreport_strategies(client):
    """Full pipeline: ingest → x-report → 12 strategies."""

    # ── Step 1: Ingest ───────────────────────────────────
    ingest_body = {
        "snapshot_version": "test_v1",
        "market_sales_review": MARKET_SALES_REVIEW_PAYLOAD,
        "population": POPULATION_PAYLOAD,
    }
    resp = await client.post("/admin/ingest", json=ingest_body)
    assert resp.status_code == 201, resp.json()
    ingest_data = resp.json()

    assert ingest_data["snapshot_version"] == "test_v1"
    # 1 store from MSR (store_id=9999001) + 1 store from population (pop_11440690)
    assert ingest_data["stores_inserted"] == 2
    assert ingest_data["documents_inserted"] == 2

    # ── Step 2: List stores to get store_source_id ──────
    resp = await client.get("/stores")
    assert resp.status_code == 200
    stores_data = resp.json()
    assert stores_data["total"] >= 2

    # Find the MSR store
    msr_store = next(s for s in stores_data["items"] if s["store_source_id"] == "9999001")
    assert msr_store["store_name"] == "테스트한식집"

    # ── Step 3: Get documents for the MSR store ─────────
    resp = await client.get(f"/stores/{msr_store['id']}/documents")
    assert resp.status_code == 200
    docs_data = resp.json()
    assert docs_data["total"] >= 1
    assert any(d["doc_type"] == "MARKET_SALES_REVIEW" for d in docs_data["items"])

    # ── Step 4: Generate X-Report ────────────────────────
    xreport_body = {
        "store_source_id": "9999001",
        "snapshot_version": "test_v1",
        "prompt_id": "test_prompt_v1",
        "force_regenerate": False,
    }
    resp = await client.post("/x-reports", json=xreport_body)
    assert resp.status_code == 201, resp.json()
    xreport_data = resp.json()

    assert xreport_data["status"] == "SUCCEEDED"
    assert xreport_data["content_text"] is not None
    assert xreport_data["config_json"] is not None
    assert xreport_data["model_meta"] is not None
    assert "tokens_in" in xreport_data["model_meta"]
    assert "cost_usd" in xreport_data["model_meta"]

    xreport_id = xreport_data["id"]

    # ── Step 5: Verify 12 strategies ─────────────────────
    resp = await client.get(f"/x-reports/{xreport_id}/strategies")
    assert resp.status_code == 200
    strat_data = resp.json()

    assert strat_data["total"] == 12, f"Expected 12 strategies, got {strat_data['total']}"

    # Verify structure: 3 groups × 4 actions
    groups = {}
    for s in strat_data["items"]:
        groups.setdefault(s["solution_group"], []).append(s)

    assert len(groups) == 3, f"Expected 3 solution_groups, got {len(groups)}"
    for g, actions in groups.items():
        assert len(actions) == 4, f"Group {g}: expected 4 actions, got {len(actions)}"
        assert g in (1, 2, 3)

    # Every strategy must have title, description, impact_mapping
    for s in strat_data["items"]:
        assert s["title"] is not None
        assert s["description"] is not None
        assert s["impact_mapping"] is not None
        assert "arrival_rate_delta" in s["impact_mapping"]
        assert "satisfaction_delta" in s["impact_mapping"]
        assert "churn_rate_delta" in s["impact_mapping"]

    # ── Step 6: Cache hit — same request should return cached ──
    resp2 = await client.post("/x-reports", json=xreport_body)
    assert resp2.status_code == 201
    # Same report id returned (cached)
    assert resp2.json()["id"] == xreport_id


@pytest.mark.asyncio
async def test_ingest_validation_error(client):
    """Invalid payload triggers 422 with path detail."""
    bad_body = {
        "snapshot_version": "bad_v1",
        "market_sales_review": [{"id": "x"}],  # missing required fields
    }
    resp = await client.post("/admin/ingest", json=bad_body)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "VALIDATION_ERROR"
    assert len(detail["errors"]) > 0


@pytest.mark.asyncio
async def test_xreport_store_not_found(client):
    """X-Report for nonexistent store returns 404."""
    body = {
        "store_source_id": "nonexistent_id",
        "snapshot_version": "test_v1",
        "prompt_id": "p1",
    }
    resp = await client.post("/x-reports", json=body)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
