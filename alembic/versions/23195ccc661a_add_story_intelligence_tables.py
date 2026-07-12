"""add_story_intelligence_tables

Revision ID: 23195ccc661a
Revises: c3a1e8f92b40
Create Date: 2026-07-12 02:20:00.000000

Phase 3 (Story Intelligence): two new tables, purely additive.
- stories: one row per evolving story (signals + bounded token signature +
  last published development).
- story_news: link table; UNIQUE(news_id) is the idempotency backbone.
No existing table, column, or row is touched. Downgrade drops only the two
new tables. Design: .claude_memory/STORY_INTELLIGENCE_ARCHITECTURE.md §11-§12.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "23195ccc661a"
down_revision: str | None = "c3a1e8f92b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("primary_category", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("central_bank", sa.String(), nullable=True),
        sa.Column("economic_event", sa.String(), nullable=True),
        sa.Column("anchor_tokens", sa.JSON(), nullable=False),
        sa.Column("latest_tokens", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("related_news_count", sa.Integer(), nullable=False),
        sa.Column("latest_news_id", sa.String(), nullable=True),
        sa.Column("latest_original_headline", sa.Text(), nullable=True),
        sa.Column("latest_headline_ar", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_stories_last_updated_at", "stories", ["last_updated_at"])

    op.create_table(
        "story_news",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "story_id",
            sa.String(),
            sa.ForeignKey("stories.id"),
            nullable=False,
        ),
        sa.Column(
            "news_id",
            sa.String(),
            sa.ForeignKey("news.id"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(), nullable=False),
        sa.Column("evidence_score", sa.Integer(), nullable=False),
        sa.Column("matching_reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("news_id", name="uq_story_news_news_id"),
    )
    op.create_index("ix_story_news_story_id", "story_news", ["story_id"])


def downgrade() -> None:
    op.drop_index("ix_story_news_story_id", table_name="story_news")
    op.drop_table("story_news")
    op.drop_index("ix_stories_last_updated_at", table_name="stories")
    op.drop_table("stories")
