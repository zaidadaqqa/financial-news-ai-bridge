"""add_indicator_memory_tables

Revision ID: 87c34e0343ed
Revises: 23195ccc661a
Create Date: 2026-07-12 13:00:00.000000

Phase 4A (Indicator Memory): two new tables, purely additive and completely
dark — nothing reads them in Phase 4A; the platform silently accumulates a
deterministic historical database of economic prints.
- indicator_series: canonical series identity + engineering quality counters.
- indicator_prints: one row per print; UNIQUE(news_id) is the idempotency
  backbone; series_id NULL = honestly unkeyed (never guessed).
No existing table, column, or row is touched. Downgrade drops only the two
new tables. Design: .claude_memory/PHASE_4_ARCHITECTURE.md (S1 / Phase 4A).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "87c34e0343ed"
down_revision: str | None = "23195ccc661a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "indicator_series",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("canonical_key", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=False),
        sa.Column("economic_event", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("unit_class", sa.String(), nullable=False),
        sa.Column("print_count", sa.Integer(), nullable=False),
        sa.Column("unit_mismatch_count", sa.Integer(), nullable=False),
        sa.Column("unknown_surprise_count", sa.Integer(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("first_print_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_print_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("canonical_key", name="uq_indicator_series_canonical_key"),
    )

    op.create_table(
        "indicator_prints",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "series_id",
            sa.String(),
            sa.ForeignKey("indicator_series.id"),
            nullable=True,
        ),
        sa.Column("news_id", sa.String(), sa.ForeignKey("news.id"), nullable=False),
        sa.Column("canonical_key", sa.String(), nullable=True),
        sa.Column("unkeyed_reason", sa.String(), nullable=True),
        sa.Column("actual_raw", sa.Text(), nullable=False),
        sa.Column("forecast_raw", sa.Text(), nullable=True),
        sa.Column("previous_raw", sa.Text(), nullable=True),
        sa.Column("actual_dec", sa.String(), nullable=True),
        sa.Column("forecast_dec", sa.String(), nullable=True),
        sa.Column("previous_dec", sa.String(), nullable=True),
        sa.Column("surprise_direction", sa.String(), nullable=False),
        sa.Column(
            "revision_of",
            sa.String(),
            sa.ForeignKey("indicator_prints.id"),
            nullable=True,
        ),
        sa.Column("print_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("news_id", name="uq_indicator_prints_news_id"),
    )
    op.create_index("ix_indicator_prints_series_id", "indicator_prints", ["series_id"])
    op.create_index("ix_indicator_prints_print_at", "indicator_prints", ["print_at"])


def downgrade() -> None:
    op.drop_index("ix_indicator_prints_print_at", table_name="indicator_prints")
    op.drop_index("ix_indicator_prints_series_id", table_name="indicator_prints")
    op.drop_table("indicator_prints")
    op.drop_table("indicator_series")
