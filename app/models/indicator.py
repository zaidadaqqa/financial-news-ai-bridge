"""Indicator Memory (Phase 4A) — the platform's silent, deterministic
historical database of economic prints.

Phase 4A is write-only and completely dark: nothing here influences any
published message, AI prompt, or the frozen Editorial Engine. Wrong history
is worse than missing history, so a print that cannot be keyed with
effectively deterministic certainty is stored UNKEYED (series_id NULL, with
the reason) — never guessed, never approximated, never merged.
Design: .claude_memory/PHASE_4_ARCHITECTURE.md (S1) + the Phase 4A owner
directives recorded in CHANGELOG.md.
"""

from datetime import datetime

from sqlalchemy import (
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


class IndicatorSeries(Base):
    __tablename__ = "indicator_series"
    __table_args__ = (
        # Canonical identity: series are keyed on canonical vocabularies
        # (the frozen intelligence engine's country + event names, plus the
        # deterministic variant/unit parsers) — never on raw headline
        # wording, so FinancialJuice rephrasing cannot fork or merge series.
        UniqueConstraint("canonical_key", name="uq_indicator_series_canonical_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    canonical_key: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    economic_event: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str] = mapped_column(String, nullable=False)  # e.g. MOM-FINAL-NSA
    unit_class: Mapped[str] = mapped_column(String, nullable=False)

    # Engineering quality counters (never user-visible): the derived score
    # lives in code (IndicatorRepository.quality_report); counters are the
    # stored facts it derives from.
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unknown_surprise_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_print_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_print_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IndicatorPrint(Base):
    __tablename__ = "indicator_prints"
    __table_args__ = (
        # One print per news item, enforced by the database — restarts and
        # duplicate task execution cannot double-write (same pattern as
        # story_news, proven in production).
        UniqueConstraint("news_id", name="uq_indicator_prints_news_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_uuid)
    series_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("indicator_series.id"), nullable=True, index=True
    )
    news_id: Mapped[str] = mapped_column(String, ForeignKey("news.id"), nullable=False)
    # Denormalized for audit and future re-keying of unkeyed prints.
    canonical_key: Mapped[str | None] = mapped_column(String, nullable=True)
    unkeyed_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # Values exactly as published (raw) plus Decimal-normalized forms
    # (string representation of decimal.Decimal; None when unparseable —
    # never approximated).
    actual_raw: Mapped[str] = mapped_column(Text, nullable=False)
    forecast_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_dec: Mapped[str | None] = mapped_column(String, nullable=True)
    forecast_dec: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_dec: Mapped[str | None] = mapped_column(String, nullable=True)
    surprise_direction: Mapped[str] = mapped_column(String, nullable=False)

    revision_of: Mapped[str | None] = mapped_column(
        String, ForeignKey("indicator_prints.id"), nullable=True
    )

    print_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
