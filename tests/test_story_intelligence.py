"""Phase 3 Story Intelligence tests: deterministic matching rules (pure
unit) and the persistent engine (async, in-memory test DB). No network, no
Telegram, no OpenAI. Real production headlines from the 2026-07-10→12 audit
are used as fixtures wherever possible."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news import NewsEvent
from app.models.story import Story, StoryNews
from app.services.intelligence.engine import classify_news
from app.services.story.engine import StoryIntelligenceEngine
from app.services.story.models import (
    MATCH_THRESHOLD,
    RelationshipType,
)
from app.services.story.rules import (
    has_correction_marker,
    repetition_overlap,
    salient_tokens,
    score_candidate,
)
from tests.conftest import TestSessionLocal

# Real production headlines (read-only audit sample).
BOJ_1 = "BoJ set to keep interest rates unchanged in July but maintain policy guidance"
BOJ_2 = "BoJ may revise up fiscal 2026 economic growth forecast in quarterly report"
GERMAN_HICP_YOY = "German HICP Final YoY Actual 2.4% (Forecast 2.4%, Previous 2.4%)"
GERMAN_HICP_MOM = "German HICP Final MoM Actual -0.2% (Forecast -0.2%, Previous -0.2%)"
FRENCH_HICP = "French HICP YoY Final Actual 2% (Forecast 2%, Previous 2.0%)"
FIDJI_1 = "OpenAI top executive Fidji Simo to resign: WSJ"
FIDJI_2 = "OpenAI top executive Fidji Simo: decided to exit full-time role"
IRAN_1 = "Mediators are trying to pull the US and Iran back from the brink - NYT"
IRAN_2 = "Qatar in talks with the US & Iran to de-escalate - NYT"
UNRELATED = "Australian housing approvals rise modestly in stable construction market"


def _story_from(headline: str, now: datetime | None = None) -> Story:
    intel = classify_news(headline)
    tokens = sorted(salient_tokens(headline))
    now = now or datetime.now(UTC)
    return Story(
        id="story-test",
        primary_category=intel.category.value,
        country=intel.country,
        currency=intel.currency,
        central_bank=intel.central_bank,
        economic_event=intel.economic_event,
        anchor_tokens=tokens,
        latest_tokens=tokens,
        first_seen_at=now,
        last_updated_at=now,
        related_news_count=1,
    )


# ---------------------------------------------------------------------------
# Matching rules (pure unit)
# ---------------------------------------------------------------------------


def test_scoring_is_deterministic() -> None:
    story = _story_from(BOJ_1)
    intel = classify_news(BOJ_2)
    tokens = salient_tokens(BOJ_2)
    results = [score_candidate(story, intel, tokens) for _ in range(5)]
    assert all(r == results[0] for r in results)


def test_strong_central_bank_match_reaches_threshold() -> None:
    story = _story_from(BOJ_1)
    intel = classify_news(BOJ_2)
    score, reasons = score_candidate(story, intel, salient_tokens(BOJ_2))
    assert score >= MATCH_THRESHOLD
    assert any("central_bank:BOJ" in r for r in reasons)


def test_event_plus_country_match_reaches_threshold() -> None:
    story = _story_from(GERMAN_HICP_YOY)
    intel = classify_news(GERMAN_HICP_MOM)
    score, _ = score_candidate(story, intel, salient_tokens(GERMAN_HICP_MOM))
    assert score >= MATCH_THRESHOLD


def test_conflicting_countries_hard_exclusion() -> None:
    # Real audit false positive: the French HICP release must never join the
    # German HICP story, despite the identical economic event.
    story = _story_from(GERMAN_HICP_YOY)
    intel = classify_news(FRENCH_HICP)
    score, reasons = score_candidate(story, intel, salient_tokens(FRENCH_HICP))
    assert score == 0
    assert reasons == ["excluded:conflicting_countries"]


def test_event_name_token_does_not_double_count() -> None:
    # "hicp" as a shared token must not re-score the event match itself.
    story = _story_from(GERMAN_HICP_YOY)
    intel = classify_news(GERMAN_HICP_MOM)
    _, reasons = score_candidate(story, intel, salient_tokens(GERMAN_HICP_MOM))
    token_reasons = [r for r in reasons if r.startswith("tokens:")]
    assert all("hicp" not in r for r in token_reasons)


def test_category_alone_cannot_match() -> None:
    story = _story_from(IRAN_1)
    intel = classify_news("Peace talks continue between two unrelated small nations")
    tokens = salient_tokens("Peace talks continue between two unrelated small nations")
    score, _ = score_candidate(story, intel, tokens - set(story.anchor_tokens))
    assert score < MATCH_THRESHOLD


def test_country_alone_cannot_match() -> None:
    story = _story_from("US housing starts data released for June period")
    intel = classify_news("US senator proposes unrelated agriculture funding bill")
    shared_free = salient_tokens(
        "US senator proposes unrelated agriculture funding bill"
    ) - set(story.anchor_tokens)
    score, _ = score_candidate(story, intel, shared_free)
    assert score < MATCH_THRESHOLD


def test_token_overlap_match_for_fallback_classified_items() -> None:
    # The Fidji Simo pair classifies as low-signal, but tokens alone carry
    # the story identity (real production repetition pair).
    story = _story_from(FIDJI_1)
    intel = classify_news(FIDJI_2)
    score, _ = score_candidate(story, intel, salient_tokens(FIDJI_2))
    assert score >= MATCH_THRESHOLD


def test_repetition_overlap_detects_reworded_headline() -> None:
    a = salient_tokens(FIDJI_1)
    b = salient_tokens(FIDJI_2)
    assert repetition_overlap(b, a) >= 0.5  # heavy overlap on the pair
    assert repetition_overlap(salient_tokens(UNRELATED), a) == 0.0


def test_correction_marker_detection() -> None:
    assert has_correction_marker("Correction: German CPI was -0.2%, not -0.3%")
    assert has_correction_marker("Agency corrects earlier headline on oil output")
    # "Revised" inside economic-data syntax is NOT a correction marker.
    assert not has_correction_marker(
        "US GDP Actual 2.1% (Forecast 2.0%, Previous -7.6%, Revised -6.6%)"
    )


def test_salient_tokens_drop_generic_words() -> None:
    tokens = salient_tokens("Oil prices rise amid market reports, says official")
    assert "rise" not in tokens
    assert "market" not in tokens
    assert "says" not in tokens
    assert "oil" in tokens


# ---------------------------------------------------------------------------
# Engine + persistence (async, test DB)
# ---------------------------------------------------------------------------


async def _insert_news(session: AsyncSession, headline: str, guid: str) -> NewsEvent:
    news = NewsEvent(
        source_message_id=guid,
        source="rss",
        source_url="https://www.financialjuice.com/x",
        original_headline=headline,
        normalized_headline=headline,
        hash=f"hash-{guid}",
    )
    session.add(news)
    await session.commit()
    return news


async def test_engine_creates_new_story() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        news = await _insert_news(session, BOJ_1, "s1")
        decision = await engine.process(news, classify_news(BOJ_1))
        assert decision is not None
        assert decision.is_new_story
        assert decision.relationship == RelationshipType.NEW_STORY
        assert decision.prior_headline_ar is None

        stories = (await session.execute(select(Story))).scalars().all()
        links = (await session.execute(select(StoryNews))).scalars().all()
        assert len(stories) == 1 and len(links) == 1
        assert links[0].relationship_type == "NEW_STORY"


async def test_engine_links_strong_same_story_and_counts() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, BOJ_1, "s1")
        d1 = await engine.process(n1, classify_news(BOJ_1))
        assert d1 is not None
        n2 = await _insert_news(session, BOJ_2, "s2")
        d2 = await engine.process(n2, classify_news(BOJ_2))
        assert d2 is not None
        assert not d2.is_new_story
        assert d2.story_id == d1.story_id
        assert d2.relationship == RelationshipType.UPDATE

        story = (await session.execute(select(Story))).scalars().one()
        assert story.related_news_count == 2


async def test_engine_prior_context_only_after_publish() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, BOJ_1, "s1")
        d1 = await engine.process(n1, classify_news(BOJ_1))
        assert d1 is not None

        # Before record_published, a follow-up has no published prior.
        n2 = await _insert_news(session, BOJ_2, "s2")
        d2 = await engine.process(n2, classify_news(BOJ_2))
        assert d2 is not None
        assert d2.prior_headline_ar is None

        # Publish n2, then a third item sees n2 as its prior development.
        n2.translated_headline = "بنك اليابان قد يرفع توقعات النمو"
        await engine.record_published(d2, n2)
        n3 = await _insert_news(
            session, "BoJ governor explains decision to keep policy guidance", "s3"
        )
        d3 = await engine.process(n3, classify_news(BOJ_1))
        assert d3 is not None
        assert d3.story_id == d1.story_id
        assert d3.prior_headline_ar == "بنك اليابان قد يرفع توقعات النمو"
        assert d3.prior_original_headline == BOJ_2


async def test_engine_idempotent_reprocessing() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        news = await _insert_news(session, BOJ_1, "s1")
        intel = classify_news(BOJ_1)
        d1 = await engine.process(news, intel)
        d2 = await engine.process(news, intel)  # restart/duplicate task
        assert d1 is not None and d2 is not None
        assert d2.story_id == d1.story_id
        assert d2.relationship == d1.relationship
        links = (await session.execute(select(StoryNews))).scalars().all()
        assert len(links) == 1  # no second link row


async def test_engine_rebuild_never_offers_item_as_its_own_prior() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        news = await _insert_news(session, BOJ_1, "s1")
        intel = classify_news(BOJ_1)
        d1 = await engine.process(news, intel)
        assert d1 is not None
        news.translated_headline = "عنوان عربي منشور"
        await engine.record_published(d1, news)
        # Reprocess after full completion: the story's latest development IS
        # this item — it must not become its own prior context.
        d2 = await engine.process(news, intel)
        assert d2 is not None
        assert d2.prior_headline_ar is None


async def test_engine_unique_constraint_is_hard_backstop() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        news = await _insert_news(session, BOJ_1, "s1")
        d1 = await engine.process(news, classify_news(BOJ_1))
        assert d1 is not None
        # Bypass the engine's soft check by adding a duplicate link directly.
        import pytest
        from sqlalchemy.exc import IntegrityError

        session.add(
            StoryNews(
                story_id=d1.story_id,
                news_id=news.id,
                relationship_type="UPDATE",
                evidence_score=5,
                matching_reasons=[],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_engine_window_expiry_creates_new_story() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        t0 = datetime.now(UTC)
        n1 = await _insert_news(session, BOJ_1, "s1")
        d1 = await engine.process(n1, classify_news(BOJ_1), now=t0)
        assert d1 is not None
        # central_bank window is 72h: one hour past → new story.
        n2 = await _insert_news(session, BOJ_2, "s2")
        d2 = await engine.process(
            n2, classify_news(BOJ_2), now=t0 + timedelta(hours=73)
        )
        assert d2 is not None
        assert d2.is_new_story
        assert d2.story_id != d1.story_id


async def test_engine_window_boundary_inside_still_matches() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        t0 = datetime.now(UTC)
        n1 = await _insert_news(session, BOJ_1, "s1")
        d1 = await engine.process(n1, classify_news(BOJ_1), now=t0)
        assert d1 is not None
        n2 = await _insert_news(session, BOJ_2, "s2")
        d2 = await engine.process(
            n2, classify_news(BOJ_2), now=t0 + timedelta(hours=71)
        )
        assert d2 is not None
        assert not d2.is_new_story


async def test_engine_routine_series_is_permanent_standalone() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        news = await _insert_news(session, "Fed Interest Rate Probabilities", "s1")
        decision = await engine.process(
            news, classify_news("Fed Interest Rate Probabilities")
        )
        assert decision is None
        stories = (await session.execute(select(Story))).scalars().all()
        assert stories == []


async def test_engine_repetition_detected_and_linked() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, FIDJI_1, "s1")
        d1 = await engine.process(n1, classify_news(FIDJI_1))
        assert d1 is not None
        n2 = await _insert_news(
            session, "OpenAI top executive Fidji Simo to resign - WSJ report", "s2"
        )
        d2 = await engine.process(n2, classify_news(FIDJI_2))
        assert d2 is not None
        assert d2.relationship == RelationshipType.REPETITION


async def test_engine_correction_detected() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, GERMAN_HICP_YOY, "s1")
        d1 = await engine.process(n1, classify_news(GERMAN_HICP_YOY))
        assert d1 is not None
        n2 = await _insert_news(
            session,
            "Correction: German HICP Final YoY was 2.5%, statistics office says",
            "s2",
        )
        d2 = await engine.process(n2, classify_news(GERMAN_HICP_YOY))
        assert d2 is not None
        assert d2.relationship == RelationshipType.CORRECTION


async def test_engine_independent_story_stays_independent() -> None:
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, IRAN_1, "s1")
        d1 = await engine.process(n1, classify_news(IRAN_1))
        n2 = await _insert_news(session, UNRELATED, "s2")
        d2 = await engine.process(n2, classify_news(UNRELATED))
        assert d1 is not None and d2 is not None
        assert d2.story_id != d1.story_id
        assert d2.is_new_story


async def test_engine_iran_diplomacy_pair_matches() -> None:
    # Real audit sequence: the US-Iran mediation chain.
    async with TestSessionLocal() as session:
        engine = StoryIntelligenceEngine(session)
        n1 = await _insert_news(session, IRAN_1, "s1")
        d1 = await engine.process(n1, classify_news(IRAN_1))
        n2 = await _insert_news(session, IRAN_2, "s2")
        d2 = await engine.process(n2, classify_news(IRAN_2))
        assert d1 is not None and d2 is not None
        assert d2.story_id == d1.story_id
        assert d2.relationship == RelationshipType.UPDATE


def test_plural_singular_tokens_match_live_false_negative_pair() -> None:
    """Real live false negative (2026-07-12, first hour on air): these two
    naturally-arriving UAE headlines split into two stories because
    'missile'/'missiles' and exact-token matching missed each other. The
    plural-normalization rule must link them."""
    live_1 = "UAE: Air defense systems currently countering missile threat"
    live_2 = "UAE: Air defences engaging missiles, drones from Iran - defence ministry"
    story = _story_from(live_1)
    intel = classify_news(live_2)
    score, reasons = score_candidate(story, intel, salient_tokens(live_2))
    assert score >= MATCH_THRESHOLD, (score, reasons)


def test_plural_normalization_is_conservative() -> None:
    from app.services.story.rules import _normalize_token

    assert _normalize_token("missiles") == "missile"
    assert _normalize_token("systems") == "system"
    assert _normalize_token("news") == "news"  # len 4 — untouched
    assert _normalize_token("gas") == "gas"
    assert _normalize_token("class") == "class"  # 'ss' — untouched
