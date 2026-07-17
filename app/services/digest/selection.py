"""Deterministic candidate selection and ranking for the six-hour digest.

No AI, no randomness: the digest's content authority is this module, and
every decision is reproducible from persisted fields alone (importance,
category, story linkage, timestamps, ids). AI never chooses what appears.

Ranking of story-level groups, in order (each factor a tiebreaker for the
previous one):

1. Breaking tier first — representative importance == 5 or
   category == "breaking".
2. Group importance (max importance among the group's eligible rows) DESC.
3. Category precedence (``CATEGORY_PRECEDENCE``): economic_data,
   central_bank, geopolitical, government ahead of company/market noise.
4. Representative ``created_at`` DESC (newer development first).
5. Representative news id ASC — a total order, so equal evidence can
   never reorder between runs.

Exclusions: importance < 3 or None (routine prints/commentary — the
principled filter; never a country/institution name ban), REPETITION
story links (same information reworded; the story's other members carry
it), rows without validated Arabic, and the feed's own product teasers
(``_PROMOTIONAL_MARKERS`` — cross-sell posts like "... - FJElite" carry
zero information and violate the no-promotion rule). Diversity: max 2
entries per category (breaking tier exempt), max 10 entries total,
never padded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.enums import NewsStatus
from app.log.logger import get_logger
from app.models.news import NewsEvent
from app.models.story import StoryNews
from app.services.digest.models import DigestEntry, DigestWindow
from app.services.story.models import RelationshipType

logger = get_logger(__name__)

MAX_ENTRIES = 10
MIN_IMPORTANCE = 3
CATEGORY_CAP = 2
SUMMARY_MIN_IMPORTANCE = 4
SUMMARY_MAX_OVERLAP = 0.6

# Explicit, frozen precedence — lower ranks earlier. Mirrors the newsroom
# priority order (breaking > data > central banks > geopolitics > fiscal >
# corporate > commodities/crypto/FX). Unknown categories sort last.
CATEGORY_PRECEDENCE: dict[str | None, int] = {
    "breaking": 0,
    "economic_data": 1,
    "central_bank": 2,
    "geopolitical": 3,
    "government": 4,
    "earnings": 5,
    "company": 6,
    "commodities": 7,
    "crypto": 8,
    "forex": 9,
    "bonds": 10,
    "general": 11,
    None: 12,
}
_DEFAULT_PRECEDENCE = 12

# Mirrors telegram_formatter._is_empty (private there, replicated to avoid
# reaching into another module's underscore API): FinancialJuice renders
# "no figure available" as dash variants, and the formatter also treats
# "0"/none-words as absent. Applied to actual/forecast/previous only.
_EMPTY_DATA_VALUES = frozenset(("none", "null", "n/a", "", "0", "-", "--", "—", "–"))

# Product markers of the feed's own promotional cross-sell posts (e.g.
# "Market reactions to US CPI ... - FJElite"). A marker exclusion, not a
# source/name ban: it targets the product branding string itself, which
# carries zero news information at any importance level.
_PROMOTIONAL_MARKERS = ("fjelite",)


def _is_promotional(*texts: str | None) -> bool:
    return any(
        marker in text.lower()
        for text in texts
        if text
        for marker in _PROMOTIONAL_MARKERS
    )


def _is_empty_data(value: str | None) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _EMPTY_DATA_VALUES


def _as_aware_utc(value: datetime) -> datetime:
    # SQLite returns stored UTC datetimes naive; re-attach UTC on read.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _token_overlap(headline: str, summary: str) -> float:
    """Share of the summary's tokens already present in the headline.

    1.0 means the summary adds nothing; an empty summary counts as fully
    redundant so it can never be selected for rendering.
    """
    summary_tokens = _tokens(summary)
    if not summary_tokens:
        return 1.0
    return len(_tokens(headline) & summary_tokens) / len(summary_tokens)


def _is_breaking(importance: int, category: str | None) -> bool:
    return importance == 5 or category == "breaking"


@dataclass(frozen=True)
class _Candidate:
    news_id: str
    story_id: str | None
    category: str | None
    importance: int
    headline_ar: str
    summary_ar: str | None
    has_data: bool
    created_at: datetime


async def select_digest_entries(
    session: AsyncSession, window: DigestWindow
) -> list[DigestEntry]:
    """Select and rank the window's story-level developments, best first.

    Returns at most ``MAX_ENTRIES`` entries; fewer than 3 (including zero)
    is a legitimate quiet-window result — padding is never added here.
    """
    stmt = (
        select(
            NewsEvent.id,
            NewsEvent.category,
            NewsEvent.importance,
            NewsEvent.original_headline,
            NewsEvent.translated_headline,
            NewsEvent.summary_ar,
            NewsEvent.actual,
            NewsEvent.forecast,
            NewsEvent.previous,
            NewsEvent.created_at,
            StoryNews.story_id,
            StoryNews.relationship_type,
        )
        .join(StoryNews, StoryNews.news_id == NewsEvent.id, isouter=True)
        .where(
            NewsEvent.status == NewsStatus.PUBLISHED,
            NewsEvent.created_at >= window.start,
            NewsEvent.created_at < window.end,
            NewsEvent.translated_headline.is_not(None),
        )
    )
    rows = (await session.execute(stmt)).all()

    excluded_repetition = 0
    excluded_low_importance = 0
    excluded_promotional = 0
    groups: dict[str, list[_Candidate]] = {}
    for (
        news_id,
        category,
        importance,
        headline_en,
        headline_ar,
        summary_ar,
        actual,
        forecast,
        previous,
        created_at,
        story_id,
        relationship_type,
    ) in rows:
        if headline_ar is None or not headline_ar.strip():
            continue
        if _is_promotional(headline_en, headline_ar):
            excluded_promotional += 1
            continue
        if relationship_type == RelationshipType.REPETITION:
            # Reworded repetition of the story's existing information —
            # the story's other members represent it. A story whose only
            # window rows are repetitions is absent, correctly: nothing
            # materially new happened in this window.
            excluded_repetition += 1
            continue
        if importance is None or importance < MIN_IMPORTANCE:
            excluded_low_importance += 1
            continue
        candidate = _Candidate(
            news_id=news_id,
            story_id=story_id,
            category=category,
            importance=importance,
            headline_ar=headline_ar.strip(),
            summary_ar=summary_ar,
            has_data=not all(_is_empty_data(v) for v in (actual, forecast, previous)),
            created_at=_as_aware_utc(created_at),
        )
        group_key = story_id if story_id is not None else f"news:{news_id}"
        groups.setdefault(group_key, []).append(candidate)

    ranked: list[tuple[tuple[int, int, int, float, str], _Candidate, int]] = []
    for members in groups.values():
        representative = min(
            members,
            key=lambda c: (-c.importance, -c.created_at.timestamp(), c.news_id),
        )
        group_importance = max(c.importance for c in members)
        breaking = _is_breaking(group_importance, representative.category)
        rank_key = (
            0 if breaking else 1,
            -group_importance,
            CATEGORY_PRECEDENCE.get(representative.category, _DEFAULT_PRECEDENCE),
            -representative.created_at.timestamp(),
            representative.news_id,
        )
        ranked.append((rank_key, representative, group_importance))
    ranked.sort(key=lambda item: item[0])

    selected: list[DigestEntry] = []
    per_category: dict[str | None, int] = {}
    capped_diversity = 0
    for rank_key, representative, group_importance in ranked:
        if len(selected) >= MAX_ENTRIES:
            break
        breaking = rank_key[0] == 0
        category_count = per_category.get(representative.category, 0)
        if not breaking and category_count >= CATEGORY_CAP:
            capped_diversity += 1
            continue
        per_category[representative.category] = category_count + 1

        summary: str | None = None
        if (
            group_importance >= SUMMARY_MIN_IMPORTANCE
            and representative.summary_ar
            and representative.summary_ar.strip()
            and _token_overlap(representative.headline_ar, representative.summary_ar)
            < SUMMARY_MAX_OVERLAP
        ):
            summary = representative.summary_ar.strip()

        selected.append(
            DigestEntry(
                news_id=representative.news_id,
                story_id=representative.story_id,
                category=representative.category,
                importance=group_importance,
                headline_ar=representative.headline_ar,
                summary_ar=summary,
                has_data=representative.has_data,
                is_breaking=breaking,
                created_at=representative.created_at,
            )
        )

    logger.debug(
        "Digest ranking detail",
        window_start=window.start.isoformat(),
        candidates=len(rows),
        groups=len(groups),
        selected=len(selected),
        excluded_repetition=excluded_repetition,
        excluded_low_importance=excluded_low_importance,
        excluded_promotional=excluded_promotional,
        capped_diversity=capped_diversity,
        scores=[
            (entry.news_id, entry.category, entry.importance, entry.is_breaking)
            for entry in selected
        ],
    )
    return selected
