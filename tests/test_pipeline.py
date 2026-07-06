from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database.connection import AsyncSessionLocal, engine
from app.models.base import Base
from app.models.news import NewsEvent, NewsStatus
from app.services.news.orchestrator import NewsOrchestrator


@pytest.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_end_to_end_pipeline() -> None:
    async with AsyncSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)

        # Mocks
        publish_mock = AsyncMock(return_value="tg_123")
        edit_mock = AsyncMock(return_value=True)
        ai_mock = AsyncMock(
            return_value={
                "translation_ar": "الترجمة",
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

        # 1. Simulate Discord Message
        await orchestrator.process_discord_message(
            message_id="msg_1",
            channel_id="chan_1",
            headline="US Non-Farm Payrolls at 100k, previous 100k, forecast 100k",
            source_url="http://test.com",
        )

        # 2. Yield control so background task runs
        import asyncio

        await asyncio.sleep(0.1)

        # 3. Assertions
        result = await session.execute(
            select(NewsEvent).filter_by(discord_message_id="msg_1")
        )
        news = result.scalars().first()

        assert news is not None
        assert news.telegram_message_id == "tg_123"
        assert news.status == NewsStatus.PUBLISHED
        assert news.translated_headline == "الترجمة"
        assert news.category == "economic_data"
        assert news.importance == 3

        # Verify Mocks were called
        publish_mock.assert_called_once()
        ai_mock.assert_called_once()
        edit_mock.assert_called_once()
