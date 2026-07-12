"""Story Intelligence engine (Phase 3): deterministic match-or-create against
persisted stories, run strictly in the background stage.

The orchestrator wraps every call in its own try/except — a failure here
degrades to exact Phase 2 behavior and never marks a news item FAILED.
Design: .claude_memory/STORY_INTELLIGENCE_ARCHITECTURE.md §5-§14.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.log.logger import get_logger
from app.models.news import NewsEvent, generate_uuid
from app.models.story import Story, StoryNews
from app.repositories.story_repository import StoryRepository
from app.services.intelligence.models import NewsIntelligenceResult
from app.services.intelligence.rules import is_routine_series
from app.services.story.models import (
    MATCH_THRESHOLD,
    REPETITION_OVERLAP_RATIO,
    STORY_TIME_WINDOWS_H,
    UNCERTAIN_SCORE,
    RelationshipType,
    StoryDecision,
)
from app.services.story.rules import (
    has_correction_marker,
    repetition_overlap,
    salient_tokens,
    score_candidate,
)

logger = get_logger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """SQLite does not round-trip timezone awareness; values written aware
    can be read back naive. All stored datetimes in this project are UTC, so
    a naive read is reinterpreted as UTC before any arithmetic."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class StoryIntelligenceEngine:
    def __init__(self, session: AsyncSession):
        self.repo = StoryRepository(session)

    async def process(
        self,
        news: NewsEvent,
        intelligence: NewsIntelligenceResult,
        now: datetime | None = None,
    ) -> StoryDecision | None:
        """Match the item to a recent story or found a new one; persist the
        link; return the decision. Returns None only for routine scheduled
        data-series items (permanent standalones — they neither create nor
        join stories, §6)."""
        if is_routine_series(news.normalized_headline):
            return None
        now = now or datetime.now(UTC)

        # Idempotency fast path (§13): reprocessing/restart/duplicate task —
        # rebuild the decision from persisted rows, write nothing.
        existing = await self.repo.get_link_by_news_id(news.id)
        if existing is not None:
            return await self._rebuild_decision(existing, news)

        tokens = salient_tokens(news.normalized_headline)

        best: tuple[int, list[str], Story] | None = None
        for story in await self.repo.get_candidates(now):
            window_h = STORY_TIME_WINDOWS_H.get(story.primary_category, 24)
            if now - _ensure_utc(story.last_updated_at) > timedelta(hours=window_h):
                continue
            score, reasons = score_candidate(story, intelligence, tokens)
            # Deterministic tie-break: candidates arrive newest-activity
            # first, and only a strictly higher score replaces the leader —
            # so equal scores resolve to the most recently updated story.
            if best is None or score > best[0]:
                best = (score, reasons, story)

        if best is not None and best[0] >= MATCH_THRESHOLD:
            return await self._link_to_story(news, tokens, now, *best)

        if best is not None and best[0] == UNCERTAIN_SCORE:
            logger.debug(
                "Story match uncertain — not linked",
                news_id=news.id[:8],
                candidate_story=best[2].id[:8],
                score=best[0],
                reasons=best[1],
            )

        return await self._create_story(news, intelligence, tokens, now)

    async def _link_to_story(
        self,
        news: NewsEvent,
        tokens: set[str],
        now: datetime,
        score: int,
        reasons: list[str],
        story: Story,
    ) -> StoryDecision:
        if has_correction_marker(news.normalized_headline):
            relationship = RelationshipType.CORRECTION
        elif (
            repetition_overlap(tokens, set(story.latest_tokens))
            >= REPETITION_OVERLAP_RATIO
        ):
            relationship = RelationshipType.REPETITION
        else:
            relationship = RelationshipType.UPDATE

        # Capture the prior published development BEFORE mutating the story —
        # this is the only material ever rendered as reader context (§16).
        prior_original = story.latest_original_headline
        prior_ar = story.latest_headline_ar
        prior_at = _ensure_utc(story.last_updated_at) if prior_original else None

        story.last_updated_at = now
        story.related_news_count = (story.related_news_count or 1) + 1
        # latest_tokens track the newest LINKED item (matching signal, not
        # reader-facing); latest_headline_* update only after PUBLISHED
        # (record_published) so rendered context is always a published prior.
        story.latest_tokens = sorted(tokens)

        link = StoryNews(
            story_id=story.id,
            news_id=news.id,
            relationship_type=relationship.value,
            evidence_score=score,
            matching_reasons=reasons,
        )
        self.repo.add(link)
        try:
            await self.repo.commit()
        except IntegrityError:
            # Backstop for the unique(news_id) constraint (§13): someone got
            # there first — reload their link and report it.
            await self.repo.rollback()
            existing = await self.repo.get_link_by_news_id(news.id)
            if existing is None:
                raise
            return await self._rebuild_decision(existing, news)

        logger.info(
            "Story matched",
            story_id=story.id[:8],
            relationship=relationship.value,
            story_items=story.related_news_count,
        )
        return StoryDecision(
            story_id=story.id,
            relationship=relationship,
            is_new_story=False,
            evidence_score=score,
            matching_reasons=tuple(reasons),
            prior_original_headline=prior_original,
            prior_headline_ar=prior_ar,
            prior_at=prior_at,
        )

    async def _create_story(
        self,
        news: NewsEvent,
        intelligence: NewsIntelligenceResult,
        tokens: set[str],
        now: datetime,
    ) -> StoryDecision:
        # id assigned eagerly — the column default fires at flush, but the
        # link row below needs the id at construction time.
        story = Story(
            id=generate_uuid(),
            primary_category=intelligence.category.value,
            country=intelligence.country,
            currency=intelligence.currency,
            central_bank=intelligence.central_bank,
            economic_event=intelligence.economic_event,
            anchor_tokens=sorted(tokens),
            latest_tokens=sorted(tokens),
            first_seen_at=now,
            last_updated_at=now,
            related_news_count=1,
        )
        self.repo.add(story)
        link = StoryNews(
            story_id=story.id,
            news_id=news.id,
            relationship_type=RelationshipType.NEW_STORY.value,
            evidence_score=0,
            matching_reasons=["new_story"],
        )
        self.repo.add(link)
        try:
            await self.repo.commit()
        except IntegrityError:
            await self.repo.rollback()
            existing = await self.repo.get_link_by_news_id(news.id)
            if existing is None:
                raise
            return await self._rebuild_decision(existing, news)

        logger.info(
            "Story created",
            story_id=story.id[:8],
            category=story.primary_category,
        )
        return StoryDecision(
            story_id=story.id,
            relationship=RelationshipType.NEW_STORY,
            is_new_story=True,
            evidence_score=0,
            matching_reasons=("new_story",),
            prior_original_headline=None,
            prior_headline_ar=None,
            prior_at=None,
        )

    async def _rebuild_decision(
        self, link: StoryNews, news: NewsEvent
    ) -> StoryDecision:
        """Reconstruct a decision from persisted rows (idempotent path).
        Guard: the story's stored latest development may already be this very
        item (record_published ran before the interruption) — never offer an
        item as its own prior context."""
        story = await self.repo.get_story(link.story_id)
        relationship = RelationshipType(link.relationship_type)
        prior_original = None
        prior_ar = None
        prior_at = None
        if (
            story is not None
            and story.latest_original_headline
            and story.latest_news_id != news.id
        ):
            prior_original = story.latest_original_headline
            prior_ar = story.latest_headline_ar
            prior_at = _ensure_utc(story.last_updated_at)
        return StoryDecision(
            story_id=link.story_id,
            relationship=relationship,
            is_new_story=relationship == RelationshipType.NEW_STORY,
            evidence_score=link.evidence_score,
            matching_reasons=tuple(link.matching_reasons or ()),
            prior_original_headline=prior_original,
            prior_headline_ar=prior_ar,
            prior_at=prior_at,
        )

    async def record_published(self, decision: StoryDecision, news: NewsEvent) -> None:
        """After the item reaches PUBLISHED: promote it to the story's latest
        published development (the future prior-context for the next item)."""
        story = await self.repo.get_story(decision.story_id)
        if story is None:
            return
        story.latest_news_id = news.id
        story.latest_original_headline = news.original_headline
        story.latest_headline_ar = news.translated_headline
        await self.repo.commit()
