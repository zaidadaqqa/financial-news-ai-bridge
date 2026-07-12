"""Persistent story model (Phase 3 — Story Intelligence).

A Story is an evolving narrative that groups related news items. StoryNews is
the link table recording which news item belongs to which story and how it
relates. Design rationale, matching semantics, and lifecycle rules:
.claude_memory/STORY_INTELLIGENCE_ARCHITECTURE.md (§5-§13).
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.news import generate_uuid, utcnow


class Story(Base):
    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    primary_category: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    central_bank: Mapped[str | None] = mapped_column(String, nullable=True)
    economic_event: Mapped[str | None] = mapped_column(String, nullable=True)

    # Bounded token signature (architecture §5): the founding headline's
    # salient tokens (anchor) and the most recent linked headline's tokens
    # (latest). Matching compares against anchor ∪ latest — never an
    # unbounded union, which the real-data audit showed snowballs into
    # topic-eating mega-stories.
    anchor_tokens: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    latest_tokens: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # The candidate-query key: bumped at link time (activity clock).
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    related_news_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # The story's last PUBLISHED development — updated only after the item
    # actually reaches PUBLISHED, so rendered "prior development" context is
    # always a previously published, validated Arabic headline (§6/§14).
    latest_news_id: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_original_headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_headline_ar: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StoryNews(Base):
    __tablename__ = "story_news"
    __table_args__ = (
        # The idempotency backbone (§13): one story per news item, enforced
        # at the database level — restarts and duplicate task execution
        # cannot create a second link.
        UniqueConstraint("news_id", name="uq_story_news_news_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    story_id: Mapped[str] = mapped_column(
        String, ForeignKey("stories.id"), nullable=False, index=True
    )
    news_id: Mapped[str] = mapped_column(String, ForeignKey("news.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    evidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matching_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
