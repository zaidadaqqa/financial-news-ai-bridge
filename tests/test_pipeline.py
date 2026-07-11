import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.constants.enums import NewsCategory, NewsStatus
from app.models.news import NewsEvent
from app.services.formatting.telegram_formatter import TelegramFormatter
from app.services.intelligence.engine import classify_news
from app.services.intelligence.models import SAFE_FALLBACK
from app.services.news.orchestrator import NewsOrchestrator
from app.services.validation.validator import OutputValidator
from tests.conftest import TestSessionLocal

MOCK_AI_RESPONSE = {
    "headline_ar": "عدد وظائف القطاع غير الزراعي الأمريكي يبلغ 100 ألف وظيفة",
    "explanation_ar": "أعلنت وزارة العمل الأمريكية أن عدد الوظائف المضافة في القطاع غير الزراعي بلغ 100 ألف وظيفة.",  # noqa: E501
    "market_impact_ar": "قد يُشير الرقم إلى تباطؤ سوق العمل مما قد يدعم توقعات خفض أسعار الفائدة.",  # noqa: E501
    "translation_ar": "الوظائف غير الزراعية الأمريكية عند 100 ألف، السابق 100 ألف، المتوقع 100 ألف",  # noqa: E501
    "summary_ar": "تعادلت بيانات سوق العمل مع التوقعات والقراءة السابقة.",
    "what_to_watch_ar": None,
    "category": "economic_data",
    "importance": 3,
    "confidence": 0.9,
    "market_bias": "POSITIVE",
    "impact": "Neutral for USD given data matches forecast",
    "affected_assets": ["USD"],
    "actual": "100",
    "forecast": "100",
    "previous": "100",
    "currency": "USD",
    "company": None,
    "ticker": None,
}


@pytest.mark.asyncio
async def test_end_to_end_pipeline() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_123")
        edit_mock = AsyncMock(return_value=True)
        ai_mock = AsyncMock(return_value=MOCK_AI_RESPONSE)

        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_message(
            source_id="9001001",
            source="rss",
            headline="US Non-Farm Payrolls at 100k, previous 100k, forecast 100k",
            source_url="http://test.com",
        )

        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9001001")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.telegram_message_id == "tg_123"
        assert news.status == NewsStatus.PUBLISHED
        assert news.translated_headline == MOCK_AI_RESPONSE["translation_ar"]
        assert news.category == "economic_data"
        assert news.importance == 3
        assert news.source == "rss"

        publish_mock.assert_called_once()
        ai_mock.assert_called_once()
        edit_mock.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_source_message_id_skipped() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_dup")
        ai_mock = AsyncMock(
            return_value={
                **MOCK_AI_RESPONSE,
                "headline_ar": "خبر تجريبي 50",
                "translation_ar": "ترجمة 50",
                "actual": "50",
                "forecast": "50",
                "previous": "50",
            }
        )
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_message(
            source_id="9002000",
            source="rss",
            headline="Test news 50k jobs",
            source_url="http://test.com/1",
        )
        await asyncio.sleep(0.1)

        await orchestrator.process_message(
            source_id="9002000",
            source="rss",
            headline="Test news 50k jobs",
            source_url="http://test.com/1",
        )
        await asyncio.sleep(0.1)

        assert (
            publish_mock.call_count == 1
        ), "Duplicate source_message_id should not be published twice"


@pytest.mark.asyncio
async def test_duplicate_content_fingerprint_skipped() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_fp")
        ai_mock = AsyncMock(
            return_value={
                **MOCK_AI_RESPONSE,
                "headline_ar": "سعر EURUSD عند 75",
                "translation_ar": "معدل EURUSD عند 75",
                "actual": "75",
                "forecast": "75",
                "previous": "75",
                "category": "forex",
                "currency": "EUR",
                "affected_assets": ["EUR", "USD"],
            }
        )
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_message(
            source_id="9003001",
            source="rss",
            headline="EURUSD rate at 75",
            source_url="http://test.com/fp",
        )
        await asyncio.sleep(0.1)

        await orchestrator.process_message(
            source_id="9003002",
            source="rss",
            headline="EURUSD rate at 75",
            source_url="http://test.com/fp",
        )
        await asyncio.sleep(0.1)

        assert (
            publish_mock.call_count == 1
        ), "Same content fingerprint should not be published twice"


@pytest.mark.asyncio
async def test_ai_failure_keeps_initial_message() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_fail")
        edit_mock = AsyncMock(return_value=True)
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("AI timed out")
        )

        await orchestrator.process_message(
            source_id="9004001",
            source="rss",
            headline="Gold prices surge 5%",
            source_url=None,
        )
        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9004001")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.telegram_message_id == "tg_fail"
        assert news.status == NewsStatus.FAILED
        assert news.last_error is not None
        publish_mock.assert_called_once()
        edit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_failure_marks_record_failed() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        orchestrator.publisher.publish_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("Telegram connection refused")
        )

        await orchestrator.process_message(
            source_id="9005001",
            source="rss",
            headline="Breaking: Fed emergency meeting",
            source_url=None,
        )
        await asyncio.sleep(0.1)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9005001")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.status == NewsStatus.FAILED
        assert news.telegram_message_id is None
        assert news.last_error is not None


# ---------------------------------------------------------------------------
# News Intelligence Engine integration (Phase 2.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_telegram_fast_path_unchanged_by_intelligence() -> None:
    """The fast path (process_message, before create_task) must not call the
    intelligence engine at all — classification only happens in the background
    task, per NEWS_INTELLIGENCE_ARCHITECTURE.md §3. The initial send must
    receive exactly the raw English text, unaffected by classification."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_fast")
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            return_value=MOCK_AI_RESPONSE
        )

        with patch("app.services.news.orchestrator.classify_news") as classify_spy:
            await orchestrator.process_message(
                source_id="9006001",
                source="rss",
                headline="US Non-Farm Payrolls at 100k",
                source_url="http://test.com",
            )
            # classify_news must not have been called yet — the fast path
            # (publish_message) already ran above, synchronously, with no
            # classification involved.
            classify_spy.assert_not_called()

        publish_mock.assert_called_once()
        sent_text = publish_mock.call_args[0][0]
        assert "US Non-Farm Payrolls at 100k" in sent_text


@pytest.mark.asyncio
async def test_classification_internal_failure_still_publishes() -> None:
    """A failure inside the engine's own rule evaluation is absorbed by
    classify_news()'s internal safety net and must never surface as a pipeline
    failure — the item must still reach PUBLISHED normally."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_intel_fail")  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            return_value=MOCK_AI_RESPONSE
        )

        with patch(
            "app.services.intelligence.engine.detect_central_bank",
            side_effect=RuntimeError("simulated internal engine bug"),
        ):
            await orchestrator.process_message(
                source_id="9007001",
                source="rss",
                headline="US Non-Farm Payrolls at 100k",
                source_url="http://test.com",
            )
            await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(source_message_id="9007001")
        )
        news = result.scalars().first()
        assert news is not None
        assert news.status == NewsStatus.PUBLISHED  # not FAILED


@pytest.mark.asyncio
async def test_ai_provider_receives_intelligence_context() -> None:
    """Proves the orchestrator actually threads the classification result into
    the AI call, not just that the pipeline still runs."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_ctx")  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ai_mock = AsyncMock(return_value=MOCK_AI_RESPONSE)
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_message(
            source_id="9008001",
            source="rss",
            headline="Fed announces emergency surprise rate cut",
            source_url="http://test.com",
        )
        await asyncio.sleep(0.2)

        ai_mock.assert_called_once()
        call_args = ai_mock.call_args[0]
        assert call_args[0] == "Fed announces emergency surprise rate cut"
        intelligence_arg = call_args[1]
        assert intelligence_arg.category == NewsCategory.CENTRAL_BANK
        assert intelligence_arg.central_bank == "FED"


def test_context_block_does_not_alter_required_ai_schema() -> None:
    """The intelligence context is appended to the request; the response schema
    OutputValidator enforces is completely untouched by this feature."""
    intelligence = classify_news("Fed announces emergency surprise rate cut")
    # The context block only affects the outbound request payload, never the
    # schema OutputValidator checks against — proven by re-validating the same
    # fixed AI response shape used throughout Phase 1, unmodified.
    OutputValidator.validate_ai_output(
        "Fed announces emergency surprise rate cut", MOCK_AI_RESPONSE
    )
    assert intelligence.category == NewsCategory.CENTRAL_BANK  # sanity: real signal


def test_formatter_uses_intelligence_category_when_confident() -> None:
    news = NewsEvent(
        source_message_id="x",
        source="rss",
        source_url="https://financialjuice.com/x",
        original_headline="test",
    )
    intelligence = classify_news("BoJ set to keep interest rates unchanged in July")
    assert intelligence.is_fallback is False

    ai_data = {
        **MOCK_AI_RESPONSE,
        "category": "forex",
    }  # AI disagrees; engine should win
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, intelligence)
    assert rendered.startswith("🏦")  # CENTRAL_BANK icon, not FOREX's default icon


def test_formatter_falls_back_to_ai_category_when_intelligence_is_fallback() -> None:
    news = NewsEvent(
        source_message_id="x",
        source="rss",
        source_url="https://financialjuice.com/x",
        original_headline="test",
    )
    ai_data = {**MOCK_AI_RESPONSE, "category": "central_bank"}
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, SAFE_FALLBACK)
    assert rendered.startswith("🏦")  # Phase 1 behavior: AI's own category is used


def test_formatter_falls_back_to_ai_category_when_intelligence_is_none() -> None:
    news = NewsEvent(
        source_message_id="x",
        source="rss",
        source_url="https://financialjuice.com/x",
        original_headline="test",
    )
    ai_data = {**MOCK_AI_RESPONSE, "category": "economic_data"}
    rendered = TelegramFormatter.format_premium_bilingual(news, ai_data, None)
    assert rendered.startswith(
        "📊"
    )  # unchanged Phase 1 behavior with no intelligence arg
