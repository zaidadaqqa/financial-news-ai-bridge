"""Deterministic digest selection and ranking: breaking tier, importance
ordering, story-level dedup, repetition exclusion, diversity caps, and
the no-filler quiet-window contract. Everything here must be exactly
reproducible — two runs over the same rows can never differ."""

from datetime import UTC, datetime, timedelta

import pytest

from app.constants.enums import NewsStatus
from app.models.news import NewsEvent
from app.models.story import Story, StoryNews
from app.services.digest.models import DigestWindow
from app.services.digest.selection import MAX_ENTRIES, select_digest_entries
from app.services.story.models import RelationshipType
from tests.conftest import TestSessionLocal

WINDOW = DigestWindow.from_start(datetime(2026, 7, 16, 6, 0, tzinfo=UTC))

_HEADLINE = "الذهب يسجل مستوى قياسيا جديدا بعد بيانات التضخم"
_DISTINCT_SUMMARY = "قرار المجلس النقدي أثار موجة شراء واسعة في العقود الآجلة"


def _news(
    guid: str,
    *,
    news_id: str | None = None,
    importance: int | None = 3,
    category: str | None = "economic_data",
    minutes: int = 30,
    headline: str | None = _HEADLINE,
    summary: str | None = None,
    status: NewsStatus = NewsStatus.PUBLISHED,
    actual: str | None = None,
) -> NewsEvent:
    event = NewsEvent(
        source_message_id=guid,
        source="rss",
        original_headline=f"original {guid}",
        normalized_headline=f"normalized {guid}",
        translated_headline=headline,
        summary_ar=summary,
        category=category,
        importance=importance,
        status=status,
        hash=f"hash-{guid}",
        actual=actual,
        created_at=WINDOW.start + timedelta(minutes=minutes),
    )
    if news_id is not None:
        event.id = news_id
    return event


async def _select(*objects: object) -> list:
    async with TestSessionLocal() as session:
        session.add_all(objects)
        await session.commit()
        return await select_digest_entries(session, WINDOW)


@pytest.mark.asyncio
async def test_breaking_importance_five_ranks_first() -> None:
    entries = await _select(
        _news("g1", importance=4, category="economic_data"),
        _news("g2", importance=5, category="general"),
    )
    assert [e.importance for e in entries] == [5, 4]
    assert entries[0].is_breaking is True
    assert entries[1].is_breaking is False


@pytest.mark.asyncio
async def test_breaking_category_ranks_first_even_at_lower_importance() -> None:
    entries = await _select(
        _news("g1", importance=4, category="central_bank"),
        _news("g2", importance=3, category="breaking"),
    )
    assert entries[0].category == "breaking"
    assert entries[0].is_breaking is True


@pytest.mark.asyncio
async def test_high_importance_ranks_above_normal() -> None:
    entries = await _select(
        _news("g1", importance=3, category="economic_data"),
        _news("g2", importance=4, category="economic_data"),
    )
    assert [e.importance for e in entries] == [4, 3]


@pytest.mark.asyncio
async def test_category_precedence_breaks_importance_ties() -> None:
    entries = await _select(
        _news("g1", importance=3, category="crypto"),
        _news("g2", importance=3, category="central_bank"),
    )
    assert [e.category for e in entries] == ["central_bank", "crypto"]


@pytest.mark.asyncio
async def test_story_grouped_to_single_entry_with_newest_max_importance() -> None:
    story = Story(id="story-1", primary_category="geopolitical")
    older_high = _news("g1", news_id="n-1", importance=4, minutes=10)
    newer_high = _news("g2", news_id="n-2", importance=4, minutes=50)
    newer_low = _news("g3", news_id="n-3", importance=3, minutes=55)
    links = [
        StoryNews(story_id="story-1", news_id=f"n-{i}", relationship_type="UPDATE")
        for i in (1, 2, 3)
    ]
    entries = await _select(story, older_high, newer_high, newer_low, *links)
    assert len(entries) == 1
    assert entries[0].news_id == "n-2"  # newest among max-importance rows
    assert entries[0].importance == 4
    assert entries[0].story_id == "story-1"


@pytest.mark.asyncio
async def test_repetition_rows_excluded_and_repetition_only_story_absent() -> None:
    story = Story(id="story-r", primary_category="economic_data")
    original = _news("g1", news_id="n-1", importance=4, minutes=10)
    repeat = _news("g2", news_id="n-2", importance=4, minutes=50)
    entries = await _select(
        story,
        original,
        repeat,
        StoryNews(story_id="story-r", news_id="n-1", relationship_type="UPDATE"),
        StoryNews(
            story_id="story-r",
            news_id="n-2",
            relationship_type=RelationshipType.REPETITION.value,
        ),
    )
    assert [e.news_id for e in entries] == ["n-1"]

    async with TestSessionLocal() as session:
        story2 = Story(id="story-only-rep", primary_category="economic_data")
        rep_only = _news("g3", news_id="n-9", importance=4)
        session.add_all(
            [
                story2,
                rep_only,
                StoryNews(
                    story_id="story-only-rep",
                    news_id="n-9",
                    relationship_type=RelationshipType.REPETITION.value,
                ),
            ]
        )
        await session.commit()
        # n-9's repetition-only story is absent; n-1 (from the same test DB
        # above) remains the only entry.
        result = await select_digest_entries(session, WINDOW)
        assert [e.news_id for e in result] == ["n-1"]


@pytest.mark.asyncio
async def test_low_and_missing_importance_excluded() -> None:
    entries = await _select(
        _news("g1", importance=2),
        _news("g2", importance=1),
        _news("g3", importance=None),
        _news("g4", importance=3),
    )
    assert len(entries) == 1
    assert entries[0].importance == 3


@pytest.mark.asyncio
async def test_unpublished_statuses_excluded() -> None:
    entries = await _select(
        _news("g1", status=NewsStatus.FAILED),
        _news("g2", status=NewsStatus.AI_FAILED),
        _news("g3", status=NewsStatus.TELEGRAM_PENDING),
        _news("g4", status=NewsStatus.AI_SUCCESS),
        _news("g5", status=NewsStatus.PUBLISHED),
    )
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_blank_arabic_headline_excluded() -> None:
    entries = await _select(
        _news("g1", headline="   "),
        _news("g2"),
    )
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_window_start_inclusive_end_exclusive() -> None:
    at_start = _news("g1", minutes=0)
    at_end = _news("g2")
    at_end.created_at = WINDOW.end
    before_start = _news("g3")
    before_start.created_at = WINDOW.start - timedelta(seconds=1)
    entries = await _select(at_start, at_end, before_start)
    assert [e.news_id for e in entries] == [at_start.id]


@pytest.mark.asyncio
async def test_deterministic_ordering_and_id_tiebreak() -> None:
    async with TestSessionLocal() as session:
        session.add_all(
            [
                _news("g1", news_id="id-b", minutes=30),
                _news("g2", news_id="id-a", minutes=30),
                _news("g3", news_id="id-c", importance=4, category="crypto"),
            ]
        )
        await session.commit()
        first = await select_digest_entries(session, WINDOW)
        second = await select_digest_entries(session, WINDOW)
    assert first == second
    # Same importance/category/time → id ascending decides, always.
    same_rank = [e.news_id for e in first if e.importance == 3]
    assert same_rank == ["id-a", "id-b"]


@pytest.mark.asyncio
async def test_diversity_cap_two_per_category() -> None:
    entries = await _select(
        _news("g1", category="commodities", minutes=10),
        _news("g2", category="commodities", minutes=20),
        _news("g3", category="commodities", minutes=30),
        _news("g4", category="forex", minutes=40),
    )
    commodities = [e for e in entries if e.category == "commodities"]
    assert len(commodities) == 2
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_breaking_exempt_from_diversity_cap() -> None:
    entries = await _select(
        _news("g1", importance=5, category="geopolitical", minutes=10),
        _news("g2", importance=5, category="geopolitical", minutes=20),
        _news("g3", importance=5, category="geopolitical", minutes=30),
    )
    assert len(entries) == 3
    assert all(e.is_breaking for e in entries)


@pytest.mark.asyncio
async def test_maximum_ten_entries() -> None:
    rows = [
        _news(f"g{i}", category=category, minutes=i)
        for i, category in enumerate(
            [
                "economic_data",
                "economic_data",
                "central_bank",
                "central_bank",
                "geopolitical",
                "geopolitical",
                "government",
                "government",
                "company",
                "company",
                "commodities",
                "commodities",
            ]
        )
    ]
    entries = await _select(*rows)
    assert len(entries) == MAX_ENTRIES


@pytest.mark.asyncio
async def test_quiet_windows_returned_unpadded() -> None:
    assert await _select() == []
    one = await _select(_news("q1"))
    assert len(one) == 1
    # Rows accumulate in the same per-test DB: q1 + q2 → exactly 2, unpadded.
    two = await _select(_news("q2", category="forex"))
    assert len(two) == 2


@pytest.mark.asyncio
async def test_summary_attached_only_when_important_and_novel() -> None:
    entries = await _select(
        _news("g1", importance=4, summary=_DISTINCT_SUMMARY, category="central_bank"),
        _news("g2", importance=4, summary=_HEADLINE, category="geopolitical"),
        _news("g3", importance=3, summary=_DISTINCT_SUMMARY, category="forex"),
    )
    by_category = {e.category: e for e in entries}
    assert by_category["central_bank"].summary_ar == _DISTINCT_SUMMARY
    assert by_category["geopolitical"].summary_ar is None  # pure paraphrase
    assert by_category["forex"].summary_ar is None  # importance 3


@pytest.mark.asyncio
async def test_promotional_product_teasers_excluded() -> None:
    promo_en = _news("g1", importance=4)
    promo_en.original_headline = "Market reactions to US CPI in 60 seconds - FJElite"
    promo_ar = _news("g2", importance=4, headline="ردود فعل السوق - fjelite")
    real = _news("g3", importance=3, category="central_bank")
    entries = await _select(promo_en, promo_ar, real)
    assert [e.category for e in entries] == ["central_bank"]
