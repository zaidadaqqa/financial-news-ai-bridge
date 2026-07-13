"""Phase 4A Indicator Memory tests: deterministic identification, honest
unkeyed storage, idempotency, quality counters, isolation, and the dark
guarantee (zero influence on published output). Real production headlines
as fixtures. No network, no Telegram, no OpenAI."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indicator import IndicatorPrint, IndicatorSeries
from app.models.news import NewsEvent
from app.repositories.indicator_repository import IndicatorRepository
from app.services.indicators.engine import IndicatorMemoryEngine
from app.services.indicators.parser import (
    identify_series,
    parse_unit_class,
    parse_variant,
)
from app.services.intelligence.engine import classify_news
from tests.conftest import TestSessionLocal

GERMAN_FINAL = "German HICP Final MoM Actual -0.2% (Forecast -0.2%, Previous -0.2%)"
CANADIAN_JOBS = "Canadian Employment Change Actual 18.2k (Forecast 10k, Previous 87.8k)"
FRENCH_NSA = "French CPI MoM NSA Actual -0.3% (Forecast -0.2%, Previous -0.2%)"
NORWEGIAN = "Norwegian CPI YoY Actual 2.7% (Forecast 3.1%, Previous 3.1%)"
WASDE = "WASDE Cotton End Stocks Actual 4.1M (Forecast 3.75M, Previous 3.7M)"
RIG_COUNT = "US Baker Hughes Total Rig Count Actual 581 (Forecast -, Previous 580)"


# ---------------------------------------------------------------------------
# Parser — deterministic or nothing
# ---------------------------------------------------------------------------


def test_variant_parsing_real_headlines() -> None:
    assert parse_variant(GERMAN_FINAL) == "MOM-FINAL-NONE"
    assert parse_variant(FRENCH_NSA) == "MOM-NONE-NSA"
    assert parse_variant(NORWEGIAN) == "YOY-NONE-NONE"
    assert parse_variant(CANADIAN_JOBS) == "NONE-NONE-NONE"


def test_variant_ambiguity_never_guesses() -> None:
    assert parse_variant("German CPI YoY and MoM combined release Actual 2%") is None


def test_unit_classes() -> None:
    assert parse_unit_class("-0.2%") == "PERCENT"
    assert parse_unit_class("18.2k") == "COUNT_K"
    assert parse_unit_class("4.1M") == "COUNT_M"
    assert parse_unit_class("581") == "BARE"
    assert parse_unit_class("not a number") is None


def test_identify_keyed_real_prints() -> None:
    identity, reason = identify_series(GERMAN_FINAL, classify_news(GERMAN_FINAL))
    assert reason is None and identity is not None
    assert identity.canonical_key == "Germany|HICP|MOM-FINAL-NONE|PERCENT"

    identity, reason = identify_series(RIG_COUNT, classify_news(RIG_COUNT))
    assert reason is None and identity is not None
    assert identity.canonical_key == "United States|Rig Count|NONE-NONE-NONE|BARE"


def test_identify_unkeyed_honestly() -> None:
    # WASDE has neither a country adjective nor a recognized event — it must
    # stay honestly unkeyed (inferring "US" from the report name would be a
    # semantic guess, rejected 2026-07-13).
    _, reason = identify_series(WASDE, classify_news(WASDE))
    assert reason == "missing_country"


def test_norwegian_prints_key_after_vocabulary_addition() -> None:
    # 2026-07-13: Norway added to the country table after real production
    # accumulation showed recurring Norwegian CPI prints honestly unkeyed.
    identity, reason = identify_series(NORWEGIAN, classify_news(NORWEGIAN))
    assert reason is None and identity is not None
    assert identity.canonical_key == "Norway|CPI|YOY-NONE-NONE|PERCENT"


def test_canonical_key_is_wording_independent() -> None:
    # The engine canonicalizes event names — both NFP phrasings key identically.
    a = "US Non-Farm Payrolls MoM Actual 150K (Forecast 140K, Previous 130K)"
    b = "US NFP MoM Actual 155K (Forecast 150K, Previous 150K)"
    ia, ra = identify_series(a, classify_news(a))
    ib, rb = identify_series(b, classify_news(b))
    assert ra is None and rb is None
    assert ia is not None and ib is not None
    assert ia.canonical_key == ib.canonical_key


# ---------------------------------------------------------------------------
# Engine + persistence
# ---------------------------------------------------------------------------


async def _insert_news(session: "AsyncSession", headline: str, guid: str) -> NewsEvent:
    news = NewsEvent(
        source_message_id=guid,
        source="rss",
        source_url="https://www.financialjuice.com/x",
        original_headline=headline,
        normalized_headline=headline,
        hash=f"h-{guid}",
    )
    session.add(news)
    await session.commit()
    return news


async def test_record_keyed_print_creates_series() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, GERMAN_FINAL, "i1")
        await engine.record(news, classify_news(GERMAN_FINAL))

        series = (await session.execute(select(IndicatorSeries))).scalars().all()
        prints = (await session.execute(select(IndicatorPrint))).scalars().all()
        assert len(series) == 1 and len(prints) == 1
        assert series[0].canonical_key == "Germany|HICP|MOM-FINAL-NONE|PERCENT"
        assert series[0].print_count == 1
        assert prints[0].series_id == series[0].id
        assert prints[0].actual_dec == "-0.2"
        assert prints[0].surprise_direction == "MATCH"


async def test_record_unkeyed_print_stored_without_series() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, WASDE, "i2")
        await engine.record(news, classify_news(WASDE))
        prints = (await session.execute(select(IndicatorPrint))).scalars().all()
        series = (await session.execute(select(IndicatorSeries))).scalars().all()
        assert len(prints) == 1 and series == []
        assert prints[0].series_id is None
        assert prints[0].unkeyed_reason == "missing_country"
        assert prints[0].actual_dec == "4100000"  # 4.1M normalized


async def test_non_structured_item_stores_nothing() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, "BoJ set to keep rates unchanged", "i3")
        await engine.record(news, classify_news("BoJ set to keep rates unchanged"))
        assert (await session.execute(select(IndicatorPrint))).scalars().all() == []


async def test_record_is_idempotent() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, GERMAN_FINAL, "i4")
        intel = classify_news(GERMAN_FINAL)
        await engine.record(news, intel)
        await engine.record(news, intel)  # restart / duplicate task
        prints = (await session.execute(select(IndicatorPrint))).scalars().all()
        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        assert len(prints) == 1
        assert series.print_count == 1


async def test_same_series_accumulates_and_counts() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        n1 = await _insert_news(session, GERMAN_FINAL, "i5")
        await engine.record(n1, classify_news(GERMAN_FINAL))
        second = "German HICP Final MoM Actual -0.1% (Forecast -0.2%, Previous -0.2%)"
        n2 = await _insert_news(session, second, "i6")
        await engine.record(n2, classify_news(second))

        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        assert series.print_count == 2
        prints = (await session.execute(select(IndicatorPrint))).scalars().all()
        assert all(p.series_id == series.id for p in prints)


async def test_dash_forecast_counts_benign_not_mismatch() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, RIG_COUNT, "i7")
        await engine.record(news, classify_news(RIG_COUNT))
        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        assert series.unknown_surprise_count == 1
        assert series.unit_mismatch_count == 0


async def test_revision_links_to_prior_print() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        n1 = await _insert_news(session, GERMAN_FINAL, "i8")
        await engine.record(n1, classify_news(GERMAN_FINAL))
        corr = (
            "Correction: German HICP Final MoM Actual -0.1% "
            "(Forecast -0.2%, Previous -0.2%)"
        )
        n2 = await _insert_news(session, corr, "i9")
        await engine.record(n2, classify_news(corr))

        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        assert series.revision_count == 1
        prints = (
            (
                await session.execute(
                    select(IndicatorPrint).order_by(IndicatorPrint.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert prints[1].revision_of == prints[0].id


async def test_quality_score_engineering_only() -> None:
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        news = await _insert_news(session, GERMAN_FINAL, "i10")
        await engine.record(news, classify_news(GERMAN_FINAL))
        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        score = IndicatorRepository.quality_score(series)
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# Isolation and the dark guarantee (Phase 4A must be invisible)
# ---------------------------------------------------------------------------


async def test_indicator_failure_never_interrupts_pipeline() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from app.constants.enums import NewsStatus
    from app.services.news.orchestrator import NewsOrchestrator
    from tests.test_pipeline import MOCK_AI_RESPONSE

    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_ind")  # type: ignore[method-assign]
        edit_mock = AsyncMock(return_value=True)
        orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            return_value=MOCK_AI_RESPONSE
        )
        orchestrator.indicator_memory.record = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("simulated indicator memory crash")
        )

        await orchestrator.process_message(
            source_id="9201001",
            source="rss",
            headline="US Non-Farm Payrolls at 100k",
            source_url="http://test.com",
        )
        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9201001")
        )
        news = result.scalars().first()
        assert news is not None
        assert news.status == NewsStatus.PUBLISHED
        edit_mock.assert_called_once()


async def test_dark_guarantee_output_identical_with_and_without_memory() -> None:
    """The rendered Telegram text must be byte-identical whether Indicator
    Memory succeeded or crashed — Phase 4A influences nothing visible."""
    import asyncio
    from unittest.mock import AsyncMock

    from app.services.news.orchestrator import NewsOrchestrator
    from tests.test_pipeline import MOCK_AI_RESPONSE

    headline = "US Non-Farm Payrolls at 100k"
    rendered: list[str] = []
    for guid, crash in (("9202001", False), ("9202002", True)):
        async with TestSessionLocal() as session:
            orchestrator = NewsOrchestrator(session)
            orchestrator.publisher.publish_message = AsyncMock(return_value="tg_d")  # type: ignore[method-assign]
            edit_mock = AsyncMock(return_value=True)
            orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
            orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
                return_value=MOCK_AI_RESPONSE
            )
            # Story engine neutralized identically in both runs: the second
            # identical headline would otherwise correctly story-match the
            # first (a Phase 3 behavior), which is not the variable under
            # test here.
            orchestrator.story_engine.process = AsyncMock(return_value=None)  # type: ignore[method-assign]
            if crash:
                orchestrator.indicator_memory.record = AsyncMock(  # type: ignore[method-assign]
                    side_effect=RuntimeError("crash")
                )
            await orchestrator.process_message(
                source_id=guid,
                source="rss",
                headline=headline,
                # Distinct URLs: the content-hash duplicate layer (correctly)
                # rejects identical headline+URL pairs.
                source_url=f"http://test.com/{guid}",
            )
            await asyncio.sleep(0.2)
            edit_mock.assert_called_once()
            text = edit_mock.call_args[0][1]
            # Neutralize the only legitimately varying token (receipt time).
            import re

            rendered.append(re.sub(r"\d{2}:\d{2} UTC", "HH:MM UTC", text))
    assert rendered[0] == rendered[1]


async def test_ai_prompt_receives_no_raw_indicator_internals() -> None:
    """Phase 4A wrote this guarantee as "the AI is completely unaware of
    Indicator Memory". Phase 4B (owner-approved) supersedes it precisely:
    the AI may receive the GATED MacroContext value object only — never the
    writer, repository, parser, or any raw internals (series IDs, canonical
    keys, quality counters, unkeyed reasons). This test enforces the new
    boundary; tests/test_macro_context.py enforces the block's content."""
    import asyncio
    import inspect
    from unittest.mock import AsyncMock

    # Static: the provider consumes only the read-model module — it must
    # never import the indicator engine, repository, or parser (no DB
    # access, no raw rows, no keying logic inside the AI layer).
    import app.services.ai.openai_provider as provider_module
    from app.services.news.orchestrator import NewsOrchestrator
    from tests.test_pipeline import MOCK_AI_RESPONSE

    source = inspect.getsource(provider_module)
    assert "indicators.context" in source  # the one allowed import
    assert "indicators.engine" not in source
    assert "indicator_repository" not in source
    assert "indicators.parser" not in source
    assert "IndicatorPrint" not in source
    assert "IndicatorSeries" not in source

    # Dynamic: a series' FIRST print has no history — the orchestrator must
    # pass macro=None and the request must look exactly like Phase 3/4A.
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_p")  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ai_mock = AsyncMock(return_value=MOCK_AI_RESPONSE)
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]
        await orchestrator.process_message(
            source_id="9203001",
            source="rss",
            headline=GERMAN_FINAL,
            source_url="http://test.com",
        )
        await asyncio.sleep(0.2)
        ai_mock.assert_called_once()
        args = ai_mock.call_args[0]
        assert len(args) == 4  # headline, intelligence, story, macro
        assert args[3] is None  # no history yet → no context, byte-identical


async def test_quality_score_never_negative() -> None:
    # Real replay finding: a series of dash-forecast prints (all
    # unknown-surprise) drove the score to -8. The 0-100 contract holds.
    async with TestSessionLocal() as session:
        engine = IndicatorMemoryEngine(session)
        n1 = await _insert_news(session, RIG_COUNT, "q1")
        await engine.record(n1, classify_news(RIG_COUNT))
        rig2 = "US Baker Hughes Total Rig Count Actual 585 (Forecast -, Previous 581)"
        n2 = await _insert_news(session, rig2, "q2")
        await engine.record(n2, classify_news(rig2))
        series = (await session.execute(select(IndicatorSeries))).scalars().one()
        assert 0 <= IndicatorRepository.quality_score(series) <= 100
