"""Add simulations and y_reports tables

Revision ID: 0002_add_simulation_y_report
Revises: 0001_initial
Create Date: 2026-02-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_add_simulation_y_report'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── simulations ──────────────────────────────────────────
    op.create_table(
        "simulations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("store_source_id", sa.String(100), nullable=False),
        sa.Column("x_report_id", sa.Integer, nullable=True),
        sa.Column("selected_strategy_ids", sa.JSON, nullable=False),
        sa.Column("days", sa.Integer, nullable=False, server_default="7"),
        sa.Column("seed", sa.Integer, nullable=False, server_default="42"),
        sa.Column("settings_json", sa.JSON, nullable=True),
        sa.Column("input_json", sa.JSON, nullable=True),
        sa.Column("output_json", sa.JSON, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_simulations_store_source_id", "simulations", ["store_source_id"])
    op.create_index("ix_simulations_x_report_id", "simulations", ["x_report_id"])

    # ── y_reports ────────────────────────────────────────────
    op.create_table(
        "y_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("store_source_id", sa.String(100), nullable=False),
        sa.Column("x_report_id", sa.Integer, nullable=True),
        sa.Column("simulation_id", sa.Integer, nullable=False),
        sa.Column("input_json", sa.JSON, nullable=True),
        sa.Column("output_json", sa.JSON, nullable=True),
        sa.Column("markdown", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_y_reports_store_source_id", "y_reports", ["store_source_id"])
    op.create_index("ix_y_reports_simulation_id", "y_reports", ["simulation_id"])


def downgrade() -> None:
    op.drop_index("ix_y_reports_simulation_id", "y_reports")
    op.drop_index("ix_y_reports_store_source_id", "y_reports")
    op.drop_table("y_reports")

    op.drop_index("ix_simulations_x_report_id", "simulations")
    op.drop_index("ix_simulations_store_source_id", "simulations")
    op.drop_table("simulations")
