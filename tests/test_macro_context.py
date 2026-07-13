"""Phase 4B Macro Context tests: deterministic historical facts computed
from Indicator Memory, minimum-history honesty gates, series isolation,
revision conservatism, the bounded AI context block, failure isolation, and
the frozen-formatter guarantee. No network, no Telegram, no OpenAI."""

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.constants.enums import NewsStatus
from app.models.news import NewsEvent
from app.services.ai.openai_provider import (
    OpenAIProvider,
    _macro_context_lines,
    _ordinal,
)
from app.services.indicators.backfill import backfill_indicator_memory
from app.services.indicators.context import (
    MAX_FACTS_IN_CONTEXT,
    MacroContext,
    MacroContextReader,
)
from app.services.indicators.engine import IndicatorMemoryEngine
from app.services.intelligence.engine import classify_news
from app.services.intelligence.models import (
    SAFE_FALLBACK,
    NewsIntelligenceResult,
)
from app.services.news.orchestrator import NewsOrchestrator
from tests.conftest import TestSessionLocal
from tests.test_ai_context import MOCK_AI_RESPONSE_JSON, _mock_httpx_response

BASE_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _hicp(actual: str, forecast: str, previous: str = "0.1") -> str:
    return (
        f"German HICP Final MoM Actual {actual}% "
        f"(Forecast {forecast}%, Previous {previous}%)"
    )


async def _add_print(
    session: Any,
    engine: IndicatorMemoryEngine,
    headline: str,
    guid: str,
    when: datetime,
    intelligence: NewsIntelligenceResult | None = None,
) -> tuple[NewsEvent, NewsIntelligenceResult]:
    news = NewsEvent(
        source_message_id=guid,
        source="rss",
        source_url="https://www.financialjuice.com/x",
        original_headline=headline,
        normalized_headline=headline,
        hash=f"h-{guid}",
        created_at=when,
    )
    session.add(news)
    await session.commit()
    intel = intelligence if intelligence is not None else classify_news(headline)
    await engine.record(news, intel)
    return news, intel


async def _series(
    session: Any,
    engine: IndicatorMemoryEngine,
    values: list[tuple[str, str]],  # (actual, forecast) pairs, chronological
    guid_prefix: str = "m",
) -> tuple[NewsEvent, NewsIntelligenceResult]:
    """Builds one German-HICP series print by print; returns the LAST one."""
    news, intel = None, None
    for i, (actual, forecast) in enumerate(values):
        news, intel = await _add_print(
            session,
            engine,
            _hicp(actual, forecast),
            f"{guid_prefix}{i}",
            BASE_AT + timedelta(days=30 * i),
        )
    assert news is not None and intel is not None
    return news, intel


# ---------------------------------------------------------------------------
# Reader — honesty gates
# ---------------------------------------------------------------------------


async def test_no_context_without_a_recorded_print() -> None:
    async with TestSessionLocal() as session:
        reader = MacroContextReader(session)
        news = NewsEvent(
            source_message_id="g1",
            source="rss",
            original_headline="Fed speaks",
            normalized_headline="Fed speaks",
            hash="h-g1",
        )
        session.add(news)
        await session.commit()
        assert await reader.read(news, SAFE_FALLBACK) is None


async def test_no_context_for_unkeyed_print() -> None:
    # Norway is honestly unkeyed (missing_country) — never used for context.
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        headline = "Norwegian CPI YoY Actual 2.7% (Forecast 3.1%, Previous 3.1%)"
        news, intel = await _add_print(session, engine, headline, "n1", BASE_AT)
        assert await MacroContextReader(session).read(news, intel) is None


async def test_no_context_for_series_first_print() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(session, engine, [("0.3", "0.2")])
        assert await MacroContextReader(session).read(news, intel) is None


async def test_two_prints_below_streak_gate_yield_nothing() -> None:
    # Streak would be 2 but the series holds only 2 prints (< 3 minimum),
    # and the headline carries its own Previous — nothing to say. Honest.
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(session, engine, [("0.3", "0.2"), ("0.4", "0.2")])
        assert await MacroContextReader(session).read(news, intel) is None


async def test_prior_print_offered_only_when_headline_lacks_previous() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        await _add_print(session, engine, _hicp("0.3", "0.2"), "p0", BASE_AT)
        # Second print whose intelligence has NO previous figure.
        base_intel = classify_news(_hicp("0.4", "0.2"))
        no_prev = dataclasses.replace(base_intel, previous=None)
        news = NewsEvent(
            source_message_id="p1",
            source="rss",
            original_headline="x",
            normalized_headline=_hicp("0.4", "0.2"),
            hash="h-p1",
            created_at=BASE_AT + timedelta(days=30),
        )
        session.add(news)
        await session.commit()
        await engine.record(news, no_prev)

        context = await MacroContextReader(session).read(news, no_prev)
        assert context is not None
        assert context.prior_actual_raw == "0.3%"
        assert context.prior_print_at is not None
        assert context.forecast_streak == 0  # 2 prints < streak minimum


async def test_above_forecast_streak() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.3", "0.2"), ("0.4", "0.3"), ("0.5", "0.4")]
        )
        context = await MacroContextReader(session).read(news, intel)
        assert context is not None
        assert context.forecast_streak == 3
        assert context.forecast_streak_direction == "above"


async def test_below_forecast_streak() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.1", "0.2"), ("0.2", "0.3"), ("0.3", "0.4")]
        )
        context = await MacroContextReader(session).read(news, intel)
        assert context is not None
        assert context.forecast_streak == 3
        assert context.forecast_streak_direction == "below"
        # 0.1 → 0.2 → 0.3 also rose twice — both facts are real.
        assert context.value_streak == 2
        assert context.value_streak_direction == "risen"


async def test_broken_forecast_streak_never_claimed() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.5", "0.2"), ("0.1", "0.3"), ("0.4", "0.3")]
        )
        # HIGHER, LOWER, HIGHER → run of 1; values 0.5→0.1→0.4 → no 2-move run.
        assert await MacroContextReader(session).read(news, intel) is None


async def test_match_forecast_is_not_a_streak() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.2", "0.2"), ("0.2", "0.2"), ("0.2", "0.2")]
        )
        assert await MacroContextReader(session).read(news, intel) is None


async def test_value_fall_streak() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        # Forecast surprises alternate (no forecast streak); values fall twice.
        news, intel = await _series(
            session, engine, [("0.5", "0.2"), ("0.3", "0.4"), ("0.1", "0.05")]
        )
        context = await MacroContextReader(session).read(news, intel)
        assert context is not None
        assert context.forecast_streak == 0
        assert context.value_streak == 2
        assert context.value_streak_direction == "fallen"


async def test_equal_value_breaks_value_streak() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.2", "0.3"), ("0.2", "0.3"), ("0.3", "0.4")]
        )
        context = await MacroContextReader(session).read(news, intel)
        # Forecast streak (3 below) is real; the value streak must not be.
        assert context is not None
        assert context.forecast_streak == 3
        assert context.value_streak == 0


async def test_extreme_needs_six_prints() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        rising = [("0.1", "0.2"), ("0.2", "0.1"), ("0.15", "0.3"), ("0.25", "0.1")]
        news, intel = await _series(session, engine, [*rising, ("0.9", "0.1")])
        context = await MacroContextReader(session).read(news, intel)
        # 5 prints: highest value but below the extreme evidence gate.
        assert context is None or context.extreme is None

        news6, intel6 = await _add_print(
            session,
            engine,
            _hicp("1.5", "0.2"),
            "x6",
            BASE_AT + timedelta(days=200),
        )
        context6 = await MacroContextReader(session).read(news6, intel6)
        assert context6 is not None
        assert context6.extreme == "highest"
        assert context6.history_count == 6


async def test_extreme_is_strict_ties_never_claimed() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        values = [
            ("0.9", "0.2"),
            ("0.1", "0.2"),
            ("0.2", "0.1"),
            ("0.3", "0.2"),
            ("0.4", "0.2"),
            ("0.9", "0.1"),  # ties the first print — not "highest"
        ]
        news, intel = await _series(session, engine, values)
        context = await MacroContextReader(session).read(news, intel)
        assert context is None or context.extreme is None


async def test_lowest_extreme() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        values = [
            ("0.9", "0.2"),
            ("0.5", "0.6"),
            ("0.7", "0.2"),
            ("0.6", "0.9"),
            ("0.8", "0.2"),
            ("-0.4", "0.5"),
        ]
        news, intel = await _series(session, engine, values)
        context = await MacroContextReader(session).read(news, intel)
        assert context is not None
        assert context.extreme == "lowest"


async def test_revision_series_is_conservative() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        await _add_print(session, engine, _hicp("0.3", "0.2"), "r0", BASE_AT)
        correction = (
            "Correction: German HICP Final MoM Actual 0.4% "
            "(Forecast 0.2%, Previous 0.1%)"
        )
        news_rev, intel_rev = await _add_print(
            session, engine, correction, "r1", BASE_AT + timedelta(days=1)
        )
        context = await MacroContextReader(session).read(news_rev, intel_rev)
        assert context is not None
        assert context.is_revision is True
        assert context.forecast_streak == 0
        assert context.value_streak == 0
        assert context.extreme is None

        # A later NORMAL print in a revision-containing series stays silent —
        # its history double-counts one period.
        news3, intel3 = await _add_print(
            session,
            engine,
            _hicp("0.5", "0.2"),
            "r2",
            BASE_AT + timedelta(days=30),
        )
        assert await MacroContextReader(session).read(news3, intel3) is None


async def test_reader_is_deterministic() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news, intel = await _series(
            session, engine, [("0.3", "0.2"), ("0.4", "0.3"), ("0.5", "0.4")]
        )
        reader = MacroContextReader(session)
        first = await reader.read(news, intel)
        second = await reader.read(news, intel)
        assert first == second


async def test_series_isolation() -> None:
    # A hot German streak must not leak into an unrelated US series.
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        await _series(session, engine, [("0.3", "0.2"), ("0.4", "0.3"), ("0.5", "0.4")])
        us = "US CPI YoY Actual 3.1% (Forecast 3.0%, Previous 2.9%)"
        news_us, intel_us = await _add_print(session, engine, us, "us1", BASE_AT)
        assert await MacroContextReader(session).read(news_us, intel_us) is None


async def test_streaks_work_in_count_units() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        template = "Canadian Employment Change Actual {}k (Forecast {}k, Previous 5k)"
        last_news, last_intel = None, None
        for i, (actual, forecast) in enumerate(
            [("10.0", "12"), ("12.5", "13"), ("18.2", "20")]
        ):
            last_news, last_intel = await _add_print(
                session,
                engine,
                template.format(actual, forecast),
                f"ca{i}",
                BASE_AT + timedelta(days=30 * i),
            )
        assert last_news is not None and last_intel is not None
        context = await MacroContextReader(session).read(last_news, last_intel)
        assert context is not None
        assert context.forecast_streak == 3  # all below forecast
        assert context.forecast_streak_direction == "below"
        assert context.value_streak == 2
        assert context.value_streak_direction == "risen"


# ---------------------------------------------------------------------------
# AI context block
# ---------------------------------------------------------------------------


def _macro(
    forecast_streak: int = 2,
    extreme: str | None = None,
    value_streak: int = 0,
    is_revision: bool = False,
    prior: str | None = None,
) -> MacroContext:
    return MacroContext(
        history_count=7,
        history_since=datetime(2026, 1, 1, tzinfo=UTC),
        forecast_streak=forecast_streak,
        forecast_streak_direction="above" if forecast_streak else None,
        value_streak=value_streak,
        value_streak_direction="fallen" if value_streak else None,
        extreme=extreme,
        is_revision=is_revision,
        prior_actual_raw=prior,
        prior_print_at=datetime(2026, 6, 1, tzinfo=UTC) if prior else None,
    )


async def _payload(
    provider: OpenAIProvider,
    headline: str,
    intelligence: object,
    macro: MacroContext | None,
) -> dict[str, Any]:
    with patch.object(
        provider.client,
        "post",
        AsyncMock(return_value=_mock_httpx_response(MOCK_AI_RESPONSE_JSON)),
    ) as post_mock:
        await provider.generate_financial_translation(
            headline, intelligence, None, macro  # type: ignore[arg-type]
        )
    payload: dict[str, Any] = post_mock.call_args.kwargs["json"]
    return payload


@pytest.fixture
def provider() -> OpenAIProvider:
    return OpenAIProvider()


@pytest.mark.asyncio
async def test_macro_block_present_with_facts(provider: OpenAIProvider) -> None:
    headline = "German HICP Final MoM Actual 0.5% (Forecast 0.4%, Previous 0.3%)"
    intelligence = classify_news(headline)
    payload = await _payload(provider, headline, intelligence, _macro())
    content = payload["messages"][1]["content"]
    assert "macro_history" in content
    assert "2nd consecutive print above forecast" in content
    assert "APPLICATION CONTEXT" in content


@pytest.mark.asyncio
async def test_macro_block_absent_when_none(provider: OpenAIProvider) -> None:
    headline = "German HICP Final MoM Actual 0.5% (Forecast 0.4%, Previous 0.3%)"
    intelligence = classify_news(headline)
    with_macro = await _payload(provider, headline, intelligence, None)
    content = with_macro["messages"][1]["content"]
    assert "macro_history" not in content
    assert "macro_fact" not in content


@pytest.mark.asyncio
async def test_macro_no_internal_leakage(provider: OpenAIProvider) -> None:
    headline = "German HICP Final MoM Actual 0.5% (Forecast 0.4%, Previous 0.3%)"
    intelligence = classify_news(headline)
    payload = await _payload(
        provider, headline, intelligence, _macro(extreme="highest")
    )
    content = payload["messages"][1]["content"]
    assert "Germany|HICP" not in content  # no canonical key
    assert "series_id" not in content
    assert "quality" not in content.lower()
    assert "unkeyed" not in content.lower()
    assert "MacroContext(" not in content
    assert "NumericSurprise." not in content


@pytest.mark.asyncio
async def test_macro_block_works_with_fallback_intelligence(
    provider: OpenAIProvider,
) -> None:
    payload = await _payload(provider, "Low signal print", SAFE_FALLBACK, _macro())
    content = payload["messages"][1]["content"]
    assert "APPLICATION CONTEXT" in content
    assert "macro_history" in content
    assert "category:" not in content  # no fabricated intelligence lines


@pytest.mark.asyncio
async def test_macro_forbids_longer_history_and_market_reaction(
    provider: OpenAIProvider,
) -> None:
    headline = "German HICP Final MoM Actual 0.5% (Forecast 0.4%, Previous 0.3%)"
    payload = await _payload(provider, headline, classify_news(headline), _macro())
    content = payload["messages"][1]["content"].lower()
    assert "forbidden" in content
    assert "market reaction" in content
    assert "within" in content and "records" in content


def test_macro_fact_lines_capped_by_priority() -> None:
    macro = _macro(
        forecast_streak=4,
        extreme="lowest",
        value_streak=3,
        is_revision=True,
        prior="0.3%",
    )
    lines = _macro_context_lines(macro)
    fact_lines = [line for line in lines if line.startswith("macro_fact:")]
    assert len(fact_lines) == MAX_FACTS_IN_CONTEXT
    assert "revision" in fact_lines[0]
    assert "lowest" in fact_lines[1]
    assert "consecutive print above forecast" in fact_lines[2]


def test_ordinals() -> None:
    assert [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 103)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
        "22nd",
        "103rd",
    ]


# ---------------------------------------------------------------------------
# Isolation and the frozen surfaces
# ---------------------------------------------------------------------------


MOCK_AI_RESPONSE = {
    "headline_ar": "الوظائف عند 100 ألف",
    "explanation_ar": "بلغ عدد الوظائف 100 ألف وظيفة.",
    "market_impact_ar": "أثر محدود عند 100 ألف.",
    "translation_ar": "الوظائف غير الزراعية الأمريكية عند 100 ألف",
    "summary_ar": "الوظائف عند 100 ألف.",
    "what_to_watch_ar": None,
    "category": "economic_data",
    "importance": 3,
    "confidence": 0.9,
    "market_bias": "NEUTRAL",
    "impact": "Limited",
    "affected_assets": ["USD"],
    "actual": "100",
    "forecast": "100",
    "previous": "100",
    "currency": "USD",
    "company": None,
    "ticker": None,
}


@pytest.mark.asyncio
async def test_macro_reader_crash_degrades_to_phase_4a_not_failed() -> None:
    """A macro-reader crash must log a warning and leave the item to
    complete exactly as Phase 4A — never FAILED, same message still edited."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_mc1")  # type: ignore[method-assign]
        edit_mock = AsyncMock(return_value=True)
        orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            return_value=MOCK_AI_RESPONSE
        )
        orchestrator.macro_reader.read = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("simulated macro reader crash")
        )

        await orchestrator.process_message(
            source_id="9202001",
            source="rss",
            headline="US Non-Farm Payrolls Actual 100K (Forecast 100K, Previous 100K)",
            source_url="http://test.com",
        )
        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9202001")
        )
        news = result.scalars().first()
        assert news is not None
        assert news.status == NewsStatus.PUBLISHED
        edit_mock.assert_called_once()
        # The AI still ran — with macro=None (fourth positional argument).
        ai_mock = orchestrator.ai_provider.generate_financial_translation
        assert ai_mock.call_args.args[3] is None  # type: ignore[union-attr]


def test_macro_never_reaches_the_fast_path_or_formatter() -> None:
    """Fast-path and formatter freeze guarantees, statically enforced:
    the initial-send path and the frozen formatter/editorial modules have
    zero knowledge of macro context."""
    import inspect

    from app.services.formatting import editorial_engine, telegram_formatter

    fast_path = inspect.getsource(NewsOrchestrator.process_message)
    assert "macro" not in fast_path
    assert "macro" not in inspect.getsource(telegram_formatter).lower()
    assert "macro" not in inspect.getsource(editorial_engine).lower()


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


async def _insert_row(
    session: Any, guid: str, headline: str, status: NewsStatus, when: datetime
) -> None:
    session.add(
        NewsEvent(
            source_message_id=guid,
            source="rss",
            original_headline=headline,
            normalized_headline=headline,
            hash=f"h-{guid}",
            status=status,
            created_at=when,
        )
    )
    await session.commit()


async def test_backfill_counts_and_idempotency() -> None:
    async with TestSessionLocal() as session:
        await _insert_row(
            session, "b1", _hicp("0.3", "0.2"), NewsStatus.PUBLISHED, BASE_AT
        )
        await _insert_row(
            session,
            "b2",
            _hicp("0.4", "0.2"),
            NewsStatus.AI_FAILED,  # reached the background stage — included
            BASE_AT + timedelta(days=30),
        )
        await _insert_row(
            session,
            "b3",
            "Norwegian CPI YoY Actual 2.7% (Forecast 3.1%, Previous 3.1%)",
            NewsStatus.PUBLISHED,
            BASE_AT + timedelta(days=31),
        )
        await _insert_row(
            session,
            "b4",
            "Fed chair speaks on inflation outlook",
            NewsStatus.PUBLISHED,
            BASE_AT + timedelta(days=32),
        )
        await _insert_row(
            session,
            "b5",
            _hicp("0.9", "0.2"),
            NewsStatus.FAILED,  # never sent — excluded by default
            BASE_AT + timedelta(days=33),
        )

        report = await backfill_indicator_memory(session)
        assert report.scanned == 4  # FAILED row not scanned
        assert report.recorded_keyed == 2
        assert report.recorded_unkeyed == 1  # Norway, honestly unkeyed
        assert report.skipped_no_actual == 1
        assert report.failed_items == 0
        assert report.first_created_at is not None

        second = await backfill_indicator_memory(session)
        assert second.recorded_keyed == 0
        assert second.recorded_unkeyed == 0
        assert second.already_recorded == 3

        with_failed = await backfill_indicator_memory(session, include_failed=True)
        assert with_failed.recorded_keyed == 1  # only the FAILED row is new


async def test_backfill_respects_limit() -> None:
    async with TestSessionLocal() as session:
        for i in range(3):
            await _insert_row(
                session,
                f"L{i}",
                _hicp(f"0.{i + 1}", "0.2"),
                NewsStatus.PUBLISHED,
                BASE_AT + timedelta(days=i),
            )
        report = await backfill_indicator_memory(session, limit=2)
        assert report.scanned == 2
