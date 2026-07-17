"""Shared value objects for the six-hour pinned digest subsystem.

Pure data — no I/O, no Telegram, no database. Every other digest module
(selection, formatter, telegram_ops, scheduler, service) builds against
the types defined here so the modules stay independently testable.

Window semantics: four fixed six-hour UTC windows per day
(00–06, 06–12, 12–18, 18–24), start inclusive, end exclusive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

WINDOW_HOURS = 6


def _as_aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class DigestWindow:
    """A six-hour UTC window: start inclusive, end exclusive."""

    start: datetime
    end: datetime

    @classmethod
    def latest_completed(cls, now: datetime | None = None) -> DigestWindow:
        """The most recent fully completed window at ``now``.

        At exactly 06:00:00 UTC the completed window is 00:00–06:00; one
        microsecond earlier it is 18:00–00:00 of the previous day.
        """
        current = _as_aware_utc(now if now is not None else datetime.now(UTC), "now")
        boundary_hour = (current.hour // WINDOW_HOURS) * WINDOW_HOURS
        end = current.replace(hour=boundary_hour, minute=0, second=0, microsecond=0)
        return cls(start=end - timedelta(hours=WINDOW_HOURS), end=end)

    @classmethod
    def from_start(cls, start: datetime) -> DigestWindow:
        aligned = _as_aware_utc(start, "start")
        return cls(start=aligned, end=aligned + timedelta(hours=WINDOW_HOURS))


def next_boundary(now: datetime | None = None) -> datetime:
    """The next window boundary strictly after ``now`` (a run moment)."""
    return DigestWindow.latest_completed(now).end + timedelta(hours=WINDOW_HOURS)


@dataclass(frozen=True)
class DigestEntry:
    """One selected story-level development, ready for rendering.

    ``headline_ar`` and ``summary_ar`` come exclusively from the news
    row's validated Arabic fields (``translated_headline``/``summary_ar``)
    — they passed number preservation and the Arabic-ratio gate when the
    item was published, so the digest never re-generates prose.
    """

    news_id: str
    story_id: str | None
    category: str | None
    importance: int
    headline_ar: str
    summary_ar: str | None
    has_data: bool
    is_breaking: bool
    created_at: datetime


class DigestRunStatus:
    """Outcome states for one digest window execution."""

    COMPLETED = "completed"
    SKIPPED_ALREADY_PROCESSED = "skipped_already_processed"
    FAILED = "failed"


@dataclass(frozen=True)
class DigestOutcome:
    status: str
    window: DigestWindow
    detail: str = ""
    message_id: str | None = None
    entry_count: int = 0
