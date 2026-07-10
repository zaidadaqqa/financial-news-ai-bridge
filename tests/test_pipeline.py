import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.constants.enums import NewsStatus
from app.models.news import NewsEvent
from app.services.news.orchestrator import NewsOrchestrator
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_end_to_end_pipeline() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_123")
        edit_mock = AsyncMock(return_value=True)
        ai_mock = AsyncMock(
            return_value={
                "translation_ar": "الترجمة 100",
                "summary_ar": "الملخص",
                "category": "economic_data",
                "importance": 3,
                "confidence": 0.9,
                "market_bias": "POSITIVE",
                "impact": "Test impact",
                "affected_assets": ["USD"],
                "actual": "100",
                "forecast": "100",
                "previous": "100",
                "currency": "USD",
                "company": "None",
                "ticker": "None",
            }
        )
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = edit_mock  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_discord_message(
            message_id="msg_1",
            channel_id="chan_1",
            headline="US Non-Farm Payrolls at 100k, previous 100k, forecast 100k",
            source_url="http://test.com",
        )

        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(discord_message_id="msg_1")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.telegram_message_id == "tg_123"
        assert news.status == NewsStatus.PUBLISHED
        assert news.translated_headline == "الترجمة 100"
        assert news.category == "economic_data"
        assert news.importance == 3

        publish_mock.assert_called_once()
        ai_mock.assert_called_once()
        edit_mock.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_discord_message_id_skipped() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_dup")
        ai_mock = AsyncMock(
            return_value={
                "translation_ar": "ترجمة 50",
                "summary_ar": "ملخص",
                "category": "economic_data",
                "importance": 2,
                "confidence": 0.8,
                "market_bias": "NEUTRAL",
                "impact": "Neutral",
                "affected_assets": [],
                "actual": "50",
                "forecast": "50",
                "previous": "50",
                "currency": None,
                "company": None,
                "ticker": None,
            }
        )
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_discord_message(
            message_id="dup_msg",
            channel_id="chan_1",
            headline="Test news 50",
            source_url="http://test.com/1",
        )
        await asyncio.sleep(0.1)

        await orchestrator.process_discord_message(
            message_id="dup_msg",
            channel_id="chan_1",
            headline="Test news 50",
            source_url="http://test.com/1",
        )
        await asyncio.sleep(0.1)

        assert (
            publish_mock.call_count == 1
        ), "Duplicate message should not be published twice"


@pytest.mark.asyncio
async def test_duplicate_content_fingerprint_skipped() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        publish_mock = AsyncMock(return_value="tg_fp")
        ai_mock = AsyncMock(
            return_value={
                "translation_ar": "ترجمة 75",
                "summary_ar": "ملخص",
                "category": "forex",
                "importance": 2,
                "confidence": 0.7,
                "market_bias": "NEUTRAL",
                "impact": "Neutral",
                "affected_assets": ["EUR"],
                "actual": "75",
                "forecast": "75",
                "previous": "75",
                "currency": "EUR",
                "company": None,
                "ticker": None,
            }
        )
        orchestrator.publisher.publish_message = publish_mock  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = ai_mock  # type: ignore[method-assign]

        await orchestrator.process_discord_message(
            message_id="msg_fp_1",
            channel_id="chan_1",
            headline="EURUSD rate at 75",
            source_url="http://test.com/fp",
        )
        await asyncio.sleep(0.1)

        await orchestrator.process_discord_message(
            message_id="msg_fp_2",
            channel_id="chan_1",
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

        await orchestrator.process_discord_message(
            message_id="msg_ai_fail",
            channel_id="chan_1",
            headline="Gold prices surge 5%",
            source_url=None,
        )
        await asyncio.sleep(0.2)

        result = await session.execute(
            select(NewsEvent).filter_by(discord_message_id="msg_ai_fail")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.telegram_message_id == "tg_fail"
        assert news.status == NewsStatus.FAILED
        publish_mock.assert_called_once()
        edit_mock.assert_not_called()


@pytest.mark.asyncio
async def test_telegram_failure_marks_record_failed() -> None:
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        orchestrator.publisher.publish_message = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("Telegram connection refused")
        )

        await orchestrator.process_discord_message(
            message_id="msg_tg_fail",
            channel_id="chan_1",
            headline="Breaking: Fed emergency meeting",
            source_url=None,
        )
        await asyncio.sleep(0.1)

        result = await session.execute(
            select(NewsEvent).filter_by(discord_message_id="msg_tg_fail")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.status == NewsStatus.FAILED
        assert news.telegram_message_id is None
