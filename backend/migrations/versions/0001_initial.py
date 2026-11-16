"""Initial schema: snapshots, stores, documents, x_reports, strategies, merged-input function

Revision ID: 0001_initial
Revises:
Create Date: 2026-02-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── data_snapshots ──────────────────────────────────
    op.create_table(
        "data_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(50), unique=True, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_data_snapshots_version", "data_snapshots", ["version"])

    # ── stores ──────────────────────────────────────────
    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("store_source_id", sa.String(50), unique=True, nullable=False),
        sa.Column("store_name", sa.String(200), nullable=False),
        sa.Column("sector_name", sa.String(100), nullable=True),
        sa.Column("area_code", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_stores_store_source_id", "stores", ["store_source_id"])

    # auto-update trigger for updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_stores_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_stores_updated_at
        BEFORE UPDATE ON stores
        FOR EACH ROW EXECUTE FUNCTION update_stores_updated_at();
    """)

    # ── store_documents ─────────────────────────────────
    op.create_table(
        "store_documents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger, sa.ForeignKey("data_snapshots.id"), nullable=False),
        sa.Column("doc_type", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("store_id", "snapshot_id", "doc_type", name="uq_store_doc"),
    )
    op.create_index(
        "ix_store_documents_payload_gin",
        "store_documents",
        ["payload"],
        postgresql_using="gin",
    )

    # CHECK constraint on doc_type
    op.execute("""
        ALTER TABLE store_documents
        ADD CONSTRAINT chk_doc_type CHECK (doc_type IN ('MARKET_SALES_REVIEW', 'POPULATION'));
    """)

    # ── x_reports ───────────────────────────────────────
    op.create_table(
        "x_reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger, sa.ForeignKey("data_snapshots.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("config_json", postgresql.JSONB, nullable=True),
        sa.Column("prompt_id", sa.String(80), nullable=False),
        sa.Column("model_meta", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.UniqueConstraint("store_id", "snapshot_id", "prompt_id", name="uq_xreport"),
    )

    # CHECK constraint on status
    op.execute("""
        ALTER TABLE x_reports
        ADD CONSTRAINT chk_xreport_status CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED'));
    """)

    # ── strategies ──────────────────────────────────────
    op.create_table(
        "strategies",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("x_report_id", sa.BigInteger, sa.ForeignKey("x_reports.id"), nullable=False),
        sa.Column("solution_group", sa.Integer, nullable=False),
        sa.Column("action_idx", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("config", postgresql.JSONB, nullable=True),
        sa.Column("impact_mapping", postgresql.JSONB, nullable=True),
        sa.UniqueConstraint("x_report_id", "solution_group", "action_idx", name="uq_strategy"),
    )

    # ── Postgres function: fn_get_merged_input ──────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_get_merged_input(p_store_id uuid, p_snapshot_id bigint)
        RETURNS jsonb AS $$
        DECLARE
            v_msr  jsonb;
            v_pop  jsonb;
        BEGIN
            SELECT payload INTO v_msr
            FROM store_documents
            WHERE store_id = p_store_id
              AND snapshot_id = p_snapshot_id
              AND doc_type = 'MARKET_SALES_REVIEW'
            LIMIT 1;

            SELECT payload INTO v_pop
            FROM store_documents
            WHERE store_id = p_store_id
              AND snapshot_id = p_snapshot_id
              AND doc_type = 'POPULATION'
            LIMIT 1;

            RETURN jsonb_build_object(
                'market_sales_review', COALESCE(v_msr, 'null'::jsonb),
                'population',          COALESCE(v_pop, 'null'::jsonb)
            );
        END;
        $$ LANGUAGE plpgsql IMMUTABLE;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fn_get_merged_input(uuid, bigint);")
    op.execute("DROP TRIGGER IF EXISTS trg_stores_updated_at ON stores;")
    op.execute("DROP FUNCTION IF EXISTS update_stores_updated_at();")
    op.drop_table("strategies")
    op.drop_table("x_reports")
    op.drop_table("store_documents")
    op.drop_table("stores")
    op.drop_table("data_snapshots")
