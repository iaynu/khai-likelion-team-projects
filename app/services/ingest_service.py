"""
Admin Ingest orchestration:
  1) Validate payloads via jsonschema
  2) Upsert snapshot version
  3) Extract stores from analysis_reports (MARKET_SALES_REVIEW) or metadata (POPULATION)
  4) Upsert stores + store_documents
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

import jsonschema
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.snapshot_repo import SnapshotRepo
from app.repositories.store_repo import StoreRepo
from app.repositories.store_document_repo import StoreDocumentRepo

logger = logging.getLogger(__name__)

_SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "..", "core", "schemas")


def _load_schema(filename: str) -> dict:
    path = os.path.join(_SCHEMA_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_MSR_SCHEMA = _load_schema("ingest_market_sales_review.json")
_POP_SCHEMA = _load_schema("ingest_population.json")


def _validate(data: Any, schema: dict, label: str) -> Optional[dict]:
    """Returns None on success, or error detail dict on failure."""
    try:
        jsonschema.validate(instance=data, schema=schema)
        return None
    except jsonschema.ValidationError as exc:
        return {
            "field": label,
            "path": list(exc.absolute_path),
            "message": exc.message,
            "schema_path": list(exc.absolute_schema_path),
        }


def _compute_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


async def run_ingest(
    session: AsyncSession,
    snapshot_version: Optional[str],
    market_sales_review: Optional[List[Any]],
    population: Optional[List[Any]],
) -> dict:
    """
    Returns {
        "snapshot_version": str,
        "stores_inserted": int,
        "stores_updated": int,
        "documents_inserted": int,
        "documents_updated": int,
        "errors": [...] | None
    }
    """
    errors: list[dict] = []

    # ── jsonschema validation ────────────────────────────
    if market_sales_review is not None:
        err = _validate(market_sales_review, _MSR_SCHEMA, "market_sales_review")
        if err:
            errors.append(err)
    if population is not None:
        err = _validate(population, _POP_SCHEMA, "population")
        if err:
            errors.append(err)
    if errors:
        return {"errors": errors}

    if market_sales_review is None and population is None:
        return {"errors": [{"field": "body", "message": "market_sales_review 또는 population 중 하나 이상 필수"}]}

    # ── snapshot version ─────────────────────────────────
    if not snapshot_version:
        snapshot_version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")

    combined = {"msr": market_sales_review, "pop": population}
    source_hash = _compute_hash(combined)

    snap_repo = SnapshotRepo(session)
    snapshot = await snap_repo.get_by_version(snapshot_version)
    if snapshot is None:
        snapshot = await snap_repo.create(snapshot_version, source_hash)

    store_repo = StoreRepo(session)
    doc_repo = StoreDocumentRepo(session)

    stores_inserted = 0
    stores_updated = 0
    docs_inserted = 0
    docs_updated = 0

    # ── process MARKET_SALES_REVIEW ──────────────────────
    if market_sales_review:
        for record in market_sales_review:
            meta = record.get("metadata", {})
            area_code = meta.get("area", "")
            sector_name = meta.get("sector")

            for report in record.get("analysis_reports", []):
                src_id = str(report["store_id"])
                name = report["store_name"]

                store, created = await store_repo.upsert(
                    store_source_id=src_id,
                    store_name=name,
                    sector_name=sector_name,
                    area_code=area_code,
                )
                if created:
                    stores_inserted += 1
                else:
                    stores_updated += 1

                # store_documents payload = the entire parent record (market context)
                _, doc_created = await doc_repo.upsert(
                    store_id=store.id,
                    snapshot_id=snapshot.id,
                    doc_type="MARKET_SALES_REVIEW",
                    payload=record,
                )
                if doc_created:
                    docs_inserted += 1
                else:
                    docs_updated += 1

    # ── process POPULATION ───────────────────────────────
    if population:
        for record in population:
            meta = record.get("metadata", {})
            area_code = meta.get("area_code", "")
            area_name = meta.get("area_name", "")

            # Population records don't carry a store_source_id per se —
            # they are keyed by area_code + quarter. Use area_code as source_id
            # so each area_code maps to one "store" row for population purposes.
            src_id = f"pop_{area_code}"

            store, created = await store_repo.upsert(
                store_source_id=src_id,
                store_name=area_name or area_code,
                area_code=area_code,
            )
            if created:
                stores_inserted += 1
            else:
                stores_updated += 1

            _, doc_created = await doc_repo.upsert(
                store_id=store.id,
                snapshot_id=snapshot.id,
                doc_type="POPULATION",
                payload=record,
            )
            if doc_created:
                docs_inserted += 1
            else:
                docs_updated += 1

    logger.info("ingest_complete", extra={"extra_fields": {
        "snapshot_version": snapshot_version,
        "stores_inserted": stores_inserted,
        "stores_updated": stores_updated,
        "docs_inserted": docs_inserted,
        "docs_updated": docs_updated,
    }})

    return {
        "snapshot_version": snapshot_version,
        "stores_inserted": stores_inserted,
        "stores_updated": stores_updated,
        "documents_inserted": docs_inserted,
        "documents_updated": docs_updated,
        "errors": None,
    }
