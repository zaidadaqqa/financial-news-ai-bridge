"""Delivery robustness (2026-07-13): persisted-error sanitization and
idempotent Telegram edit retries — both anchored to a real production
incident (a delivered Maersk item wrongly FAILED, with the bot token
persisted inside its last_error). No network, no real Telegram."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.constants.enums import NewsStatus
from app.exceptions.custom_exceptions import AIResponseError
from app.models.news import NewsEvent
from app.models.story import StoryNews
from app.services.news.orchestrator import NewsOrchestrator, _sanitize_error
from app.services.telegram.publisher import TelegramPublisher
from tests.conftest import TestSessionLocal
from tests.test_pipeline import MOCK_AI_RESPONSE

FAKE_TOKEN_ERROR = (
    "Client error '400 Bad Request' for url 'https://api.telegram.org/"
    "bot1234567890:AAFakeTokenForTests_only-123/editMessageText'"
)


def test_sanitize_error_redacts_token_and_url() -> None:
    sanitized = _sanitize_error(RuntimeError(FAKE_TOKEN_ERROR))
    assert "1234567890:AAFakeTokenForTests" not in sanitized
    assert "bot<redacted>" in sanitized or "<url>" in sanitized
    assert "RuntimeError" in sanitized
    assert len(sanitized) <= 120


def _response(status_code: int, text: str) -> SimpleNamespace:
    def raise_for_status() -> None:
        request = httpx.Request("POST", "https://example.invalid")
        response = httpx.Response(status_code, request=request)
        if status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    return SimpleNamespace(
        status_code=status_code,
        text=text,
        raise_for_status=raise_for_status,
        json=lambda: {"ok": True},
    )


@pytest.mark.asyncio
async def test_edit_retry_not_modified_counts_as_success() -> None:
    publisher = TelegramPublisher()
    publisher.client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(400, "Bad Request: message is not modified")
    )
    assert await publisher.edit_message("42", "text") is True


@pytest.mark.asyncio
async def test_edit_other_400_still_raises() -> None:
    publisher = TelegramPublisher()
    publisher.client.post = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(400, "Bad Request: chat not found")
    )
    with pytest.raises(httpx.HTTPStatusError):
        await publisher.edit_message("42", "text")


@pytest.mark.asyncio
async def test_persisted_last_error_never_contains_token() -> None:
    """An AI-stage failure whose message embeds a token-bearing URL must be
    persisted redacted — the exact leak found in production."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        orchestrator.publisher.publish_message = AsyncMock(return_value="tg_san1")  # type: ignore[method-assign]
        orchestrator.publisher.edit_message = AsyncMock(return_value=True)  # type: ignore[method-assign]
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            side_effect=AIResponseError(FAKE_TOKEN_ERROR)
        )
        await orchestrator.process_message(
            source_id="9301001",
            source="rss",
            headline="Maersk: The WAF6 service will now transit via the Cape",
            source_url="http://test.com",
        )
        await asyncio.sleep(0.2)
        news = (
            (
                await session.execute(
                    select(NewsEvent).filter_by(source_message_id="9301001")
                )
            )
            .scalars()
            .one()
        )
        assert news.status == NewsStatus.AI_FAILED
        assert news.last_error is not None
        assert "1234567890:AAFakeTokenForTests" not in news.last_error
        assert "api.telegram.org" not in news.last_error


@pytest.mark.asyncio
async def test_not_modified_retry_item_publishes_and_anchors_its_story() -> None:
    """Agent A's knock-on: a delivered-but-mislabeled item was invisible to
    the published-priors story gate. With the idempotent-retry fix the item
    ends PUBLISHED and is recorded as its story's development."""
    async with TestSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        orchestrator.publisher.publish_message = AsyncMock(return_value="77")  # type: ignore[method-assign]
        orchestrator.publisher.client.post = AsyncMock(  # type: ignore[method-assign]
            return_value=_response(400, "Bad Request: message is not modified")
        )
        orchestrator.ai_provider.generate_financial_translation = AsyncMock(  # type: ignore[method-assign]
            return_value=MOCK_AI_RESPONSE
        )
        await orchestrator.process_message(
            source_id="9301002",
            source="rss",
            headline="US Non-Farm Payrolls Actual 100K (Forecast 100K, Previous 100K)",
            source_url="http://test.com",
        )
        await asyncio.sleep(0.3)
        news = (
            (
                await session.execute(
                    select(NewsEvent).filter_by(source_message_id="9301002")
                )
            )
            .scalars()
            .one()
        )
        assert news.status == NewsStatus.PUBLISHED
        link = (
            (await session.execute(select(StoryNews).filter_by(news_id=news.id)))
            .scalars()
            .first()
        )
        assert link is not None  # anchored in its story → eligible as prior
